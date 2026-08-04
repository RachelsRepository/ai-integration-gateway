"""Agent execution use case.

Implements a bounded reason/act loop: the model is called, any tool calls it emits are
executed against the registry, results are fed back, and the cycle repeats until the model
answers or the iteration budget is exhausted.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from ai_gateway.application.dto import AgentRunCommand, AgentRunResult, RequestContext
from ai_gateway.application.ports.llm_provider import (
    ProviderCallContext,
    ProviderChatRequest,
    ProviderChatResponse,
    ToolSchema,
)
from ai_gateway.application.ports.repositories import UnitOfWork
from ai_gateway.application.ports.tools import ToolExecutionContext
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.domain.entities.agent import (
    AgentDefinition,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepType,
    ToolInvocation,
)
from ai_gateway.domain.entities.conversation import Conversation
from ai_gateway.domain.entities.message import Message, MessageRole, ToolCall
from ai_gateway.domain.entities.tenant import Permission
from ai_gateway.domain.entities.usage import OperationType
from ai_gateway.domain.errors import (
    AgentExecutionError,
    DomainError,
    NotFoundError,
    ToolError,
    ToolNotFoundError,
)
from ai_gateway.domain.events import DomainEvent, EventType
from ai_gateway.domain.policies.routing import RoutingDecision, RoutingStrategy
from ai_gateway.domain.services.content_safety import RiskLevel
from ai_gateway.domain.value_objects.identifiers import AgentRunId
from ai_gateway.domain.value_objects.model import ModelCapability, ModelRef

_TOOL_OUTPUT_LIMIT = 8_000
_TOOL_ARGS_LIMIT = 8_000
_MAX_TOOL_DEPTH = 8


class RunAgentUseCase:
    """Serves ``POST /v1/agents/run``."""

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators.
        """
        self._s = services

    async def execute(self, command: AgentRunCommand, context: RequestContext) -> AgentRunResult:
        """Execute an agent until it answers or exhausts its budget.

        Args:
            command: Caller request.
            context: Request context.

        Returns:
            The run result, including every recorded step.

        Raises:
            DomainError: On authorisation, quota or unrecoverable provider failures.
        """
        context.principal.require(Permission.AGENTS_RUN)
        context.tenant.assert_active()
        await self._s.meter.enforce_rate_limit(context.tenant, cost=2)

        definition = self._definition(command)
        run = AgentRun(tenant_id=context.tenant_id, definition=definition)
        run.metadata["input"] = command.input
        if command.metadata.get("pause_after_steps"):
            run.metadata["pause_after_steps"] = str(command.metadata["pause_after_steps"])
        started = self._s.clock.monotonic()

        async with self._s.uow_factory() as uow:
            conversation = await self._load_conversation(uow, command, context)
            run.conversation_id = conversation.id if conversation else None
            transcript = self._seed_transcript(definition, command, conversation)
            verdict = self._s.guardrails.screen_request(tuple(transcript), context.tenant)
            transcript = list(verdict.messages)
            run.start()
            await uow.agent_runs.save(run)
            await uow.commit()

            try:
                await self._loop(uow, run, transcript, command, context)
            except DomainError as exc:
                run.fail(exc.message, now=self._s.clock.now())
                await self._finalise(
                    uow,
                    run,
                    context,
                    conversation=conversation,
                    transcript=transcript,
                    started=started,
                )
                raise

            if run.metadata.get("paused") == "true":
                await uow.commit()
            else:
                await self._finalise(
                    uow,
                    run,
                    context,
                    conversation=conversation,
                    transcript=transcript,
                    started=started,
                )
                await uow.commit()

        return AgentRunResult(
            run_id=run.id,
            request_id=context.request_id,
            status=run.status,
            output=run.output,
            steps=tuple(run.steps),
            usage=run.total_usage,
            cost=run.total_cost,
            latency_ms=int((self._s.clock.monotonic() - started) * 1000),
            conversation_id=run.conversation_id,
            error=run.error,
        )

    async def resume(self, run_id: str, context: RequestContext) -> AgentRunResult:
        """Resume a durable agent run after a pause or process restart."""
        context.principal.require(Permission.AGENTS_RUN)
        context.tenant.assert_active()
        started = self._s.clock.monotonic()
        async with self._s.uow_factory() as uow:
            run = await uow.agent_runs.get(AgentRunId(run_id), tenant_id=context.tenant_id)
            if run is None:
                raise NotFoundError("Agent run not found", details={"run_id": run_id})
            if run.status not in {AgentRunStatus.RUNNING, AgentRunStatus.PENDING}:
                raise AgentExecutionError(
                    "Agent run is not resumable",
                    details={"run_id": run_id, "status": run.status.value},
                )
            conversation = None
            if run.conversation_id is not None:
                conversation = await uow.conversations.get(
                    run.conversation_id, tenant_id=context.tenant_id
                )
            command = AgentRunCommand(
                input=run.metadata.get("input", ""),
                agent_name=run.definition.name,
                instructions=run.definition.instructions,
                # frozenset → sorted tuple keeps resume tool order deterministic
                tools=tuple(sorted(run.definition.tools)),
                model=run.definition.model,
                max_iterations=run.definition.max_iterations,
                conversation_id=run.conversation_id,
                metadata={k: v for k, v in run.metadata.items() if k != "pause_after_steps"},
            )
            transcript = self._seed_transcript(run.definition, command, conversation)
            # Rebuild tool/assistant messages already recorded as steps.
            for step in run.steps:
                if step.content:
                    transcript.append(Message.assistant(step.content))
                for inv in step.tool_invocations:
                    transcript.append(inv.to_message())
            run.metadata.pop("paused", None)
            try:
                await self._loop(uow, run, transcript, command, context)
            except DomainError as exc:
                run.fail(exc.message, now=self._s.clock.now())
                await self._finalise(
                    uow,
                    run,
                    context,
                    conversation=conversation,
                    transcript=transcript,
                    started=started,
                )
                raise
            if run.metadata.get("paused") == "true":
                await uow.commit()
            else:
                await self._finalise(
                    uow,
                    run,
                    context,
                    conversation=conversation,
                    transcript=transcript,
                    started=started,
                )
                await uow.commit()
        return AgentRunResult(
            run_id=run.id,
            request_id=context.request_id,
            status=run.status,
            output=run.output,
            steps=tuple(run.steps),
            usage=run.total_usage,
            cost=run.total_cost,
            latency_ms=int((self._s.clock.monotonic() - started) * 1000),
            conversation_id=run.conversation_id,
            error=run.error,
        )

    # ------------------------------------------------------------------ loop
    async def _loop(
        self,
        uow: UnitOfWork,
        run: AgentRun,
        transcript: list[Message],
        command: AgentRunCommand,
        context: RequestContext,
    ) -> None:
        tool_schemas = self._tool_schemas(run.definition)
        call_context = self._s.call_context(context)

        while run.iterations_remaining > 0:
            decision = self._route(run, transcript, command, context, bool(tool_schemas))
            response = await self._call_model(
                run,
                decision,
                transcript,
                tools=tool_schemas,
                command=command,
                call_context=call_context,
            )
            step_cost = self._s.meter.price(decision.selected.ref, response.usage)

            if not response.tool_calls:
                run.record_step(
                    AgentStep(
                        index=run.next_step_index,
                        type=AgentStepType.FINAL_ANSWER,
                        started_at=self._s.clock.now(),
                        usage=response.usage,
                        cost=step_cost,
                        content=response.content,
                    )
                )
                transcript.append(response.message)
                run.succeed(
                    self._s.guardrails.filter_output(response.content), now=self._s.clock.now()
                )
                await uow.agent_runs.save(run)
                await uow.commit()
                return

            transcript.append(response.message)
            invocations = await self._execute_tools(uow, run, response.tool_calls, context)
            run.record_step(
                AgentStep(
                    index=run.next_step_index,
                    type=AgentStepType.TOOL_CALL,
                    started_at=self._s.clock.now(),
                    usage=response.usage,
                    cost=step_cost,
                    content=response.content or None,
                    tool_invocations=tuple(invocations),
                )
            )
            transcript.extend(invocation.to_message() for invocation in invocations)
            # Durable mid-run checkpoint so a restarted worker/API can resume.
            await uow.agent_runs.save(run)
            await uow.commit()
            pause_after = int(command.metadata.get("pause_after_steps", "0") or "0")
            if pause_after > 0 and len(run.steps) >= pause_after:
                run.metadata["paused"] = "true"
                run.metadata["resume_transcript_len"] = str(len(transcript))
                await uow.agent_runs.save(run)
                await uow.commit()
                return

        run.fail("Agent exhausted its iteration budget", now=self._s.clock.now())
        raise AgentExecutionError(
            "Agent exhausted its iteration budget",
            details={"run_id": run.id, "max_iterations": run.definition.max_iterations},
        )

    async def _call_model(
        self,
        run: AgentRun,
        decision: RoutingDecision,
        transcript: list[Message],
        *,
        tools: tuple[ToolSchema, ...],
        command: AgentRunCommand,
        call_context: ProviderCallContext,
    ) -> ProviderChatResponse:
        request = ProviderChatRequest(
            model=decision.selected.ref,
            messages=tuple(transcript),
            max_output_tokens=command.max_output_tokens,
            temperature=command.temperature,
            tools=tools,
            tool_choice=run.definition.tool_choice if tools else "none",
        )
        outcome = await self._s.executor.chat(decision.chain, request, call_context)
        run.metadata["model"] = outcome.model.qualified
        return replace(outcome.value, model=outcome.model)

    def _route(
        self,
        run: AgentRun,
        transcript: list[Message],
        command: AgentRunCommand,
        context: RequestContext,
        needs_tools: bool,
    ) -> RoutingDecision:
        capabilities = {ModelCapability.CHAT}
        if needs_tools:
            capabilities.add(ModelCapability.TOOL_CALLING)
        return self._s.router.route(
            preferences=context.tenant.routing,
            capabilities=frozenset(capabilities),
            strategy=RoutingStrategy.BALANCED,
            requested_model=command.model or run.definition.model,
            estimated_prompt_tokens=self._s.estimator.estimate_messages(transcript),
            max_output_tokens=command.max_output_tokens,
        )

    async def _execute_tools(
        self,
        uow: UnitOfWork,
        run: AgentRun,
        calls: tuple[ToolCall, ...],
        context: RequestContext,
    ) -> list[ToolInvocation]:
        invocations: list[ToolInvocation] = []
        for call in calls:
            invocation = await self._execute_tool(run, call, context)
            invocations.append(invocation)
            await uow.outbox.enqueue(
                DomainEvent(
                    type=EventType.TOOL_EXECUTED,
                    tenant_id=context.tenant_id,
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    payload={
                        "run_id": run.id,
                        "tool": call.name,
                        "succeeded": invocation.succeeded,
                        "duration_ms": invocation.duration_ms,
                    },
                )
            )
        return invocations

    async def _execute_tool(  # noqa: PLR0911
        self, run: AgentRun, call: ToolCall, context: RequestContext
    ) -> ToolInvocation:
        started = time.perf_counter()
        if call.name not in run.definition.tools:
            return ToolInvocation(
                call=call,
                output="",
                succeeded=False,
                error=f"Tool {call.name!r} is not permitted for this agent",
            )
        try:
            tool = self._s.tools.get(call.name)
        except ToolNotFoundError as exc:
            return ToolInvocation(call=call, output="", succeeded=False, error=exc.message)

        if tool.definition.requires_confirmation and not run.metadata.get("tools_confirmed"):
            return ToolInvocation(
                call=call,
                output="",
                succeeded=False,
                error=f"Tool {call.name!r} requires confirmation before execution",
            )

        # Constrained runner: reject oversized argument payloads and nested shell-like keys.
        encoded_args = str(call.arguments)
        if len(encoded_args) > _TOOL_ARGS_LIMIT:
            return ToolInvocation(
                call=call,
                output="",
                succeeded=False,
                error="tool arguments exceed size limit",
            )
        forbidden = {"__import__", "eval", "exec", "subprocess", "os.system", "/bin/", "shell"}
        lowered = encoded_args.lower()
        if any(token in lowered for token in forbidden):
            return ToolInvocation(
                call=call,
                output="",
                succeeded=False,
                error="tool arguments rejected by constrained execution policy",
            )

        depth = int(run.metadata.get("tool_depth", "0") or "0")
        if depth >= _MAX_TOOL_DEPTH:
            return ToolInvocation(
                call=call,
                output="",
                succeeded=False,
                error="recursive tool-call limit exceeded",
            )
        run.metadata["tool_depth"] = str(depth + 1)

        execution_context = ToolExecutionContext(
            tenant_id=context.tenant_id,
            request_id=context.request_id,
            agent_run_id=run.id,
            deadline_seconds=tool.definition.timeout_seconds,
        )
        try:
            async with asyncio.timeout(tool.definition.timeout_seconds):
                raw = await tool.execute(dict(call.arguments), execution_context)
        except TimeoutError:
            return ToolInvocation(
                call=call,
                output="",
                succeeded=False,
                duration_ms=self._elapsed_ms(started),
                error="tool execution timed out",
            )
        except (ToolError, DomainError) as exc:
            return ToolInvocation(
                call=call,
                output="",
                succeeded=False,
                duration_ms=self._elapsed_ms(started),
                error=exc.message,
            )

        output = raw[:_TOOL_OUTPUT_LIMIT]
        screening = self._s.guardrails.screen_tool_output(output)
        if screening.risk is RiskLevel.HIGH:
            return ToolInvocation(
                call=call,
                output="",
                succeeded=False,
                duration_ms=self._elapsed_ms(started),
                error="tool output rejected by injection screening",
            )
        return ToolInvocation(
            call=call, output=output, succeeded=True, duration_ms=self._elapsed_ms(started)
        )

    # ------------------------------------------------------------------ helpers
    def _definition(self, command: AgentRunCommand) -> AgentDefinition:
        available = set(self._s.tools.names())
        requested = set(command.tools)
        unknown = requested - available
        if unknown:
            raise NotFoundError(
                "Requested tools are not registered", details={"tools": sorted(unknown)}
            )
        return AgentDefinition(
            name=command.agent_name,
            instructions=command.instructions,
            tools=frozenset(requested),
            max_iterations=command.max_iterations,
            model=command.model,
            temperature=command.temperature,
            max_output_tokens=command.max_output_tokens,
        )

    def _tool_schemas(self, definition: AgentDefinition) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(
                name=tool.name, description=tool.description, parameters=tool.parameters_schema
            )
            for tool in self._s.tools.definitions(sorted(definition.tools))
        )

    async def _load_conversation(
        self, uow: UnitOfWork, command: AgentRunCommand, context: RequestContext
    ) -> Conversation | None:
        if command.conversation_id is None:
            return None
        conversation = await uow.conversations.get(
            command.conversation_id, tenant_id=context.tenant_id
        )
        if conversation is None:
            raise NotFoundError(
                "Conversation not found",
                details={"conversation_id": command.conversation_id},
            )
        conversation.assert_owned_by(context.tenant_id)
        return conversation

    @staticmethod
    def _seed_transcript(
        definition: AgentDefinition,
        command: AgentRunCommand,
        conversation: Conversation | None,
    ) -> list[Message]:
        transcript: list[Message] = [Message.system(definition.instructions)]
        if conversation is not None:
            transcript.extend(
                m
                for m in conversation.history(limit=definition.memory_window)
                if m.role is not MessageRole.SYSTEM
            )
        transcript.append(Message.user(command.input))
        return transcript

    async def _finalise(
        self,
        uow: UnitOfWork,
        run: AgentRun,
        context: RequestContext,
        *,
        conversation: Conversation | None,
        transcript: list[Message],
        started: float,
    ) -> None:
        latency_ms = int((self._s.clock.monotonic() - started) * 1000)
        await uow.agent_runs.save(run)

        if conversation is not None:
            now = self._s.clock.now()
            existing = {m.id for m in conversation.messages}
            conversation.extend(
                [
                    m
                    for m in transcript
                    if m.role is not MessageRole.SYSTEM and m.id not in existing
                ],
                now=now,
            )
            if run.output:
                conversation.append(Message.assistant(run.output), now=now)
            conversation.record_usage(run.total_usage, now=now)
            await uow.conversations.save(conversation)

        await self._s.meter.record(
            uow,
            context,
            operation=OperationType.AGENT,
            model=self._served_model(run),
            usage=run.total_usage,
            cost=run.total_cost,
            latency_ms=latency_ms,
            succeeded=run.output is not None,
            metadata={"agent": run.definition.name, "steps": str(len(run.steps))},
        )
        await uow.outbox.enqueue(
            DomainEvent(
                type=EventType.AGENT_RUN_COMPLETED,
                tenant_id=context.tenant_id,
                request_id=context.request_id,
                trace_id=context.trace_id,
                payload={
                    "run_id": run.id,
                    "status": run.status.value,
                    "steps": len(run.steps),
                    "tokens": run.total_usage.as_dict(),
                    "cost_micros": run.total_cost.micros,
                    "latency_ms": latency_ms,
                },
            )
        )
        await self._s.audit.record(
            uow,
            context,
            action="agents.run",
            resource=run.definition.name,
            attributes={
                "run_id": run.id,
                "status": run.status.value,
                "steps": len(run.steps),
                "tools": sorted(run.definition.tools),
            },
        )

    def _served_model(self, run: AgentRun) -> ModelRef:
        """Return the model that actually served the run.

        Args:
            run: The completed or failed run.

        Returns:
            The last model used, falling back to the pinned or first catalogued model.

        Raises:
            AgentExecutionError: If no model could be determined.
        """
        recorded = run.metadata.get("model") or run.definition.model
        if recorded:
            return ModelRef.parse(recorded)
        first = next(iter(self._s.catalog.all()), None)
        if first is None:
            raise AgentExecutionError("No model catalogue entries are configured")
        return first.ref

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)


__all__ = ["RunAgentUseCase"]
