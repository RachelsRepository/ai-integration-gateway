"""Chat completion use case, including streamed delivery."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

from ai_gateway.application.dto import (
    ChatCompletionCommand,
    ChatCompletionResult,
    RequestContext,
    RoutingTrace,
    StreamEvent,
    StreamEventType,
)
from ai_gateway.application.ports.llm_provider import (
    ProviderChatRequest,
    ToolSchema,
)
from ai_gateway.application.ports.repositories import UnitOfWork
from ai_gateway.application.services.guardrails import GuardrailVerdict
from ai_gateway.application.services.quota_ledger import QuotaReservation
from ai_gateway.application.use_cases.base import GatewayServices, load_prompt, require_messages
from ai_gateway.domain.entities.audit import AuditOutcome
from ai_gateway.domain.entities.conversation import Conversation
from ai_gateway.domain.entities.message import FinishReason, Message, MessageRole
from ai_gateway.domain.entities.tenant import Permission
from ai_gateway.domain.entities.usage import OperationType
from ai_gateway.domain.errors import DomainError, NotFoundError
from ai_gateway.domain.events import DomainEvent, EventType
from ai_gateway.domain.policies.routing import RoutingDecision
from ai_gateway.domain.value_objects.identifiers import ConversationId
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.tokens import TokenUsage


@dataclass(frozen=True, slots=True)
class _PreparedRequest:
    """Everything resolved before the first upstream call is made."""

    messages: tuple[Message, ...]
    provider_request: ProviderChatRequest
    decision: RoutingDecision
    conversation: Conversation | None
    guardrails: GuardrailVerdict
    estimated_prompt_tokens: int
    reservation: QuotaReservation | None = None


class ChatCompletionUseCase:
    """Serves ``POST /v1/chat/completions``.

    The use case owns the full request lifecycle: authorisation, rate limiting, prompt
    resolution, guardrails, routing, quota enforcement, resilient execution, output
    filtering, conversation persistence, metering and auditing.
    """

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators resolved by the composition root.
        """
        self._s = services

    async def execute(
        self, command: ChatCompletionCommand, context: RequestContext
    ) -> ChatCompletionResult:
        """Generate a complete, non-streamed chat completion.

        Args:
            command: Caller request.
            context: Request context.

        Returns:
            The completion result.

        Raises:
            DomainError: On any policy violation or upstream failure.
        """
        context.principal.require(Permission.CHAT_INVOKE)
        context.tenant.assert_active()
        await self._s.meter.enforce_rate_limit(context.tenant)

        started = self._s.clock.monotonic()
        async with self._s.uow_factory() as uow:
            prepared = await self._prepare(uow, command, context)
            call_context = self._s.call_context(context)

            if command.cache and self._s.response_cache.is_cacheable(prepared.provider_request):
                cached = await self._s.response_cache.get(
                    context.tenant_id, prepared.provider_request
                )
                if cached is not None:
                    return await self._finish_cached(
                        uow,
                        command,
                        context,
                        prepared,
                        cached_content=cached.content,
                        cached_model=cached.model,
                        cached_usage=cached.usage,
                        started=started,
                    )

            try:
                outcome = await self._s.executor.chat(
                    prepared.decision.chain, prepared.provider_request, call_context
                )
            except DomainError as exc:
                await self._s.meter.release_reservation(prepared.reservation)
                await self._record_failure(uow, context, prepared, exc)
                await uow.commit()
                raise

            content = self._s.guardrails.filter_output(outcome.value.message.content)
            message = outcome.value.message.with_content(content)
            usage = outcome.value.usage
            cost = self._s.meter.price(outcome.model, usage)
            latency_ms = int((self._s.clock.monotonic() - started) * 1000)
            await self._s.meter.settle_reservation(
                prepared.reservation,
                actual_tokens=usage.total_tokens,
                actual_cost=cost,
            )

            if command.cache and self._s.response_cache.is_cacheable(prepared.provider_request):
                await self._s.response_cache.set(
                    context.tenant_id,
                    prepared.provider_request,
                    content=content,
                    model=outcome.model,
                    usage=usage,
                    finish_reason=outcome.value.finish_reason,
                )

            conversation_id = await self._persist_conversation(
                uow, prepared, message, usage, context
            )
            await self._s.meter.record(
                uow,
                context,
                operation=OperationType.CHAT,
                model=outcome.model,
                usage=usage,
                cost=cost,
                latency_ms=latency_ms,
                attempt=outcome.total_attempts,
                metadata={"fallback": str(outcome.fallback_used).lower()},
            )
            await uow.outbox.enqueue(
                DomainEvent(
                    type=EventType.COMPLETION_RECEIVED,
                    tenant_id=context.tenant_id,
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    payload={
                        "model": outcome.model.qualified,
                        "finish_reason": outcome.value.finish_reason.value,
                        "tokens": usage.as_dict(),
                        "latency_ms": latency_ms,
                        "fallback_used": outcome.fallback_used,
                    },
                )
            )
            await self._s.audit.record(
                uow,
                context,
                action="chat.completion",
                outcome=AuditOutcome.ALLOWED,
                resource=outcome.model.qualified,
                attributes={
                    "tokens": usage.total_tokens,
                    "cost_micros": cost.micros,
                    "redactions": prepared.guardrails.redaction_count,
                    "injection_risk": prepared.guardrails.injection.risk.value,
                },
            )
            await uow.commit()

        return ChatCompletionResult(
            request_id=context.request_id,
            model=outcome.model,
            message=message,
            usage=usage,
            cost=cost,
            finish_reason=outcome.value.finish_reason,
            latency_ms=latency_ms,
            routing=self._trace(prepared.decision, outcome.attempts, outcome.fallback_used),
            conversation_id=conversation_id,
        )

    async def stream(
        self, command: ChatCompletionCommand, context: RequestContext
    ) -> AsyncIterator[StreamEvent]:
        """Generate a streamed chat completion.

        Args:
            command: Caller request.
            context: Request context.

        Yields:
            Stream events in delivery order, terminating with ``done`` or ``error``.
        """
        context.principal.require(Permission.CHAT_INVOKE)
        context.tenant.assert_active()
        await self._s.meter.enforce_rate_limit(context.tenant)

        started = self._s.clock.monotonic()
        index = 0
        buffer: list[str] = []
        usage = TokenUsage.empty()
        finish_reason = FinishReason.STOP
        served_by: ModelRef | None = None

        async with self._s.uow_factory() as uow:
            prepared = await self._prepare(uow, replace(command, stream=True), context)
            call_context = self._s.call_context(context)
            yield StreamEvent(
                type=StreamEventType.START,
                index=index,
                data={
                    "request_id": context.request_id,
                    "model": prepared.decision.selected.ref.qualified,
                },
            )
            index += 1

            try:
                async for model_ref, chunk in self._s.executor.stream_chat(
                    prepared.decision.chain, prepared.provider_request, call_context
                ):
                    served_by = model_ref
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.finish_reason is not None:
                        finish_reason = chunk.finish_reason
                    if chunk.tool_call_delta is not None:
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL,
                            index=index,
                            data=chunk.tool_call_delta,
                        )
                        index += 1
                    if chunk.delta:
                        buffer.append(chunk.delta)
                        yield StreamEvent(
                            type=StreamEventType.DELTA,
                            index=index,
                            data={"content": chunk.delta},
                        )
                        index += 1
            except DomainError as exc:
                await self._s.meter.release_reservation(prepared.reservation)
                await self._record_failure(uow, context, prepared, exc)
                await uow.commit()
                yield StreamEvent(type=StreamEventType.ERROR, index=index, data=exc.to_dict())
                return

            model = served_by or prepared.decision.selected.ref
            content = self._s.guardrails.filter_output("".join(buffer))
            estimated_usage = False
            if usage.total_tokens == 0:
                usage = self._estimate_usage(prepared, content)
                estimated_usage = True
            cost = self._s.meter.price(model, usage)
            latency_ms = int((self._s.clock.monotonic() - started) * 1000)
            await self._s.meter.settle_reservation(
                prepared.reservation,
                actual_tokens=usage.total_tokens,
                actual_cost=cost,
            )

            message = Message(role=MessageRole.ASSISTANT, content=content)
            conversation_id = await self._persist_conversation(
                uow, prepared, message, usage, context
            )
            await self._s.meter.record(
                uow,
                context,
                operation=OperationType.CHAT,
                model=model,
                usage=usage,
                cost=cost,
                latency_ms=latency_ms,
                metadata={"streamed": "true"},
                estimated=estimated_usage,
            )
            await self._s.audit.record(
                uow,
                context,
                action="chat.completion.stream",
                resource=model.qualified,
                attributes={"tokens": usage.total_tokens, "cost_micros": cost.micros},
            )
            await uow.commit()

        yield StreamEvent(
            type=StreamEventType.USAGE,
            index=index,
            data={
                "model": model.qualified,
                "usage": usage.as_dict(),
                "cost_micros": cost.micros,
                "currency": cost.currency,
            },
        )
        yield StreamEvent(
            type=StreamEventType.DONE,
            index=index + 1,
            data={
                "finish_reason": finish_reason.value,
                "latency_ms": latency_ms,
                "conversation_id": conversation_id,
            },
        )

    # ------------------------------------------------------------------ internals
    async def _prepare(
        self, uow: UnitOfWork, command: ChatCompletionCommand, context: RequestContext
    ) -> _PreparedRequest:
        messages, conversation = await self._resolve_messages(uow, command, context)
        verdict = self._s.guardrails.screen_request(messages, context.tenant)
        estimated = self._s.estimator.estimate_messages(list(verdict.messages))

        await uow.outbox.enqueue(
            DomainEvent(
                type=EventType.PROMPT_SUBMITTED,
                tenant_id=context.tenant_id,
                request_id=context.request_id,
                trace_id=context.trace_id,
                payload={
                    "messages": len(verdict.messages),
                    "estimated_prompt_tokens": estimated,
                    "prompt_name": command.prompt_name,
                    "stream": command.stream,
                    "redactions": verdict.redaction_count,
                },
            )
        )

        decision = self._s.router.route(
            preferences=context.tenant.routing,
            capabilities=command.required_capabilities,
            strategy=command.routing_strategy,
            requested_model=command.model,
            estimated_prompt_tokens=estimated,
            max_output_tokens=command.max_output_tokens,
            max_candidates=4 if command.allow_fallback else 1,
        )
        projected = self._s.meter.project(
            decision.selected.ref,
            prompt_tokens=estimated,
            max_output_tokens=command.max_output_tokens,
        )
        await self._s.meter.enforce_quota(
            uow,
            context.tenant,
            projected_tokens=estimated + command.max_output_tokens,
            projected_cost=projected,
        )
        reservation = await self._s.meter.reserve(
            context.tenant,
            reservation_id=str(context.request_id),
            projected_tokens=estimated + command.max_output_tokens,
            projected_cost=projected,
            model=decision.selected.ref,
            concurrency_limit=context.tenant.rate_limit_burst or None,
        )
        await uow.outbox.enqueue(
            DomainEvent(
                type=EventType.PROVIDER_SELECTED,
                tenant_id=context.tenant_id,
                request_id=context.request_id,
                trace_id=context.trace_id,
                payload={
                    "model": decision.selected.ref.qualified,
                    "provider": decision.selected.provider.value,
                    "strategy": decision.strategy.value,
                    "reason": decision.reason,
                    "fallbacks": [c.ref.qualified for c in decision.fallbacks],
                    "rejected": decision.rejected,
                    "projected_cost_micros": projected.micros,
                },
            )
        )

        provider_request = ProviderChatRequest(
            model=decision.selected.ref,
            messages=verdict.messages,
            max_output_tokens=command.max_output_tokens,
            temperature=command.temperature,
            top_p=command.top_p,
            stop=command.stop,
            tools=tuple(
                ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
                for t in command.tools
            ),
            tool_choice=command.tool_choice,
            response_format=command.response_format,
            seed=command.seed,
            metadata=dict(command.metadata),
        )
        return _PreparedRequest(
            messages=verdict.messages,
            provider_request=provider_request,
            decision=decision,
            conversation=conversation,
            guardrails=verdict,
            estimated_prompt_tokens=estimated,
            reservation=reservation,
        )

    async def _resolve_messages(
        self, uow: UnitOfWork, command: ChatCompletionCommand, context: RequestContext
    ) -> tuple[tuple[Message, ...], Conversation | None]:
        messages: list[Message] = []
        conversation: Conversation | None = None

        if command.conversation_id is not None:
            conversation = await uow.conversations.get(
                command.conversation_id, tenant_id=context.tenant_id
            )
            if conversation is None:
                raise NotFoundError(
                    "Conversation not found",
                    details={"conversation_id": command.conversation_id},
                )
            conversation.assert_owned_by(context.tenant_id)
            messages.extend(conversation.history(limit=40))

        if command.prompt_name:
            context.principal.require(Permission.PROMPTS_READ)
            prompt = await load_prompt(uow, context.tenant_id, command.prompt_name)
            rendered = prompt.render(command.prompt_variables, version=command.prompt_version)
            messages.extend(rendered.as_messages())

        messages.extend(command.messages)
        return require_messages(tuple(messages)), conversation

    async def _persist_conversation(
        self,
        uow: UnitOfWork,
        prepared: _PreparedRequest,
        assistant: Message,
        usage: TokenUsage,
        context: RequestContext,
    ) -> ConversationId | None:
        conversation = prepared.conversation
        if conversation is None:
            return None
        new_turns = [m for m in prepared.messages if m.role is not MessageRole.SYSTEM]
        existing = {m.id for m in conversation.messages}
        conversation.extend([m for m in new_turns if m.id not in existing], now=self._s.clock.now())
        conversation.append(assistant, now=self._s.clock.now())
        conversation.record_usage(usage, now=self._s.clock.now())
        await uow.conversations.save(conversation)
        await uow.outbox.enqueue(
            DomainEvent(
                type=EventType.CONVERSATION_UPDATED,
                tenant_id=context.tenant_id,
                request_id=context.request_id,
                payload={
                    "conversation_id": conversation.id,
                    "messages": len(conversation.messages),
                    "cumulative_tokens": conversation.cumulative_usage.total_tokens,
                },
            )
        )
        return conversation.id

    async def _record_failure(
        self,
        uow: UnitOfWork,
        context: RequestContext,
        prepared: _PreparedRequest,
        error: DomainError,
    ) -> None:
        await uow.outbox.enqueue(
            DomainEvent(
                type=EventType.PROVIDER_FAILED,
                tenant_id=context.tenant_id,
                request_id=context.request_id,
                trace_id=context.trace_id,
                payload={
                    "model": prepared.decision.selected.ref.qualified,
                    "provider": prepared.decision.selected.provider.value,
                    "error_code": error.code,
                    "details": error.details,
                },
            )
        )
        await self._s.audit.record(
            uow,
            context,
            action="chat.completion",
            outcome=AuditOutcome.FAILED,
            resource=prepared.decision.selected.ref.qualified,
            attributes={"error_code": error.code},
        )

    async def _finish_cached(
        self,
        uow: UnitOfWork,
        command: ChatCompletionCommand,
        context: RequestContext,
        prepared: _PreparedRequest,
        *,
        cached_content: str,
        cached_model: str,
        cached_usage: TokenUsage,
        started: float,
    ) -> ChatCompletionResult:
        model = ModelRef.parse(cached_model)
        message = Message(role=MessageRole.ASSISTANT, content=cached_content)
        latency_ms = int((self._s.clock.monotonic() - started) * 1000)
        conversation_id = await self._persist_conversation(
            uow, prepared, message, cached_usage, context
        )
        await self._s.meter.record(
            uow,
            context,
            operation=OperationType.CHAT,
            model=model,
            usage=cached_usage,
            cost=Money.zero(),
            latency_ms=latency_ms,
            cached=True,
            metadata={"cache": "hit"},
        )
        await self._s.audit.record(
            uow,
            context,
            action="chat.completion",
            resource=model.qualified,
            attributes={"cache": "hit", "tokens": cached_usage.total_tokens},
        )
        await uow.commit()
        return ChatCompletionResult(
            request_id=context.request_id,
            model=model,
            message=message,
            usage=cached_usage,
            cost=Money.zero(),
            finish_reason=FinishReason.STOP,
            latency_ms=latency_ms,
            routing=RoutingTrace(
                selected_model=model.qualified,
                strategy=command.routing_strategy,
                reason="served from response cache",
                attempts=(model.qualified,),
            ),
            cached=True,
            conversation_id=conversation_id,
        )

    def _estimate_usage(self, prepared: _PreparedRequest, content: str) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=prepared.estimated_prompt_tokens,
            completion_tokens=self._s.estimator.estimate_text(content),
        )

    @staticmethod
    def _trace(
        decision: RoutingDecision, attempts: tuple[str, ...], fallback_used: bool
    ) -> RoutingTrace:
        return RoutingTrace(
            selected_model=attempts[-1] if attempts else decision.selected.ref.qualified,
            strategy=decision.strategy,
            reason=decision.reason,
            attempts=attempts,
            fallback_used=fallback_used,
            rejected=decision.rejected,
        )


__all__ = ["ChatCompletionUseCase"]
