"""Embeddings use case."""

from __future__ import annotations

from ai_gateway.application.dto import EmbeddingsCommand, EmbeddingsResult, RequestContext
from ai_gateway.application.ports.llm_provider import EmbeddingsRequest
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.domain.entities.tenant import Permission
from ai_gateway.domain.entities.usage import OperationType
from ai_gateway.domain.errors import ValidationError
from ai_gateway.domain.value_objects.model import ModelCapability
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.tokens import TokenUsage

_MAX_BATCH = 256


class EmbeddingsUseCase:
    """Serves ``POST /v1/embeddings``.

    Inputs are looked up in a per-tenant embedding cache first; only cache misses reach a
    provider, and the results are re-assembled in the caller's original input order.
    """

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators.
        """
        self._s = services

    async def execute(
        self, command: EmbeddingsCommand, context: RequestContext
    ) -> EmbeddingsResult:
        """Embed one or more texts.

        Args:
            command: Caller request.
            context: Request context.

        Returns:
            The embedding vectors and their metering data.

        Raises:
            ValidationError: If the batch is empty or exceeds the batch ceiling.
        """
        context.principal.require(Permission.EMBEDDINGS_INVOKE)
        context.tenant.assert_active()
        self._validate(command)
        await self._s.meter.enforce_rate_limit(context.tenant)

        started = self._s.clock.monotonic()
        estimated = sum(self._s.estimator.estimate_text(text) for text in command.inputs)

        async with self._s.uow_factory() as uow:
            decision = self._s.router.route(
                preferences=context.tenant.routing,
                capabilities=frozenset({ModelCapability.EMBEDDINGS}),
                strategy=command.routing_strategy,
                requested_model=command.model,
                estimated_prompt_tokens=estimated,
                max_output_tokens=1,
            )
            model = decision.selected.ref
            await self._s.meter.enforce_quota(
                uow,
                context.tenant,
                projected_tokens=estimated,
                projected_cost=self._s.meter.project(
                    model, prompt_tokens=estimated, max_output_tokens=0
                ),
            )

            cached = (
                await self._s.embedding_cache.get_many(context.tenant_id, model, command.inputs)
                if command.cache
                else {}
            )
            pending = [
                (index, text) for index, text in enumerate(command.inputs) if index not in cached
            ]

            vectors: dict[int, tuple[float, ...]] = dict(cached)
            usage = TokenUsage.empty()
            cost = Money.zero()

            if pending:
                request = EmbeddingsRequest(
                    model=model,
                    inputs=tuple(text for _, text in pending),
                    dimensions=command.dimensions,
                )
                outcome = await self._s.executor.embed(
                    decision.chain, request, self._s.call_context(context)
                )
                for (index, _), vector in zip(pending, outcome.value.vectors, strict=False):
                    vectors[index] = vector
                usage = outcome.value.usage
                cost = self._s.meter.price(outcome.model, usage)
                model = outcome.model
                if command.cache:
                    await self._s.embedding_cache.put_many(
                        context.tenant_id,
                        model,
                        [(text, vectors[index]) for index, text in pending if index in vectors],
                    )

            latency_ms = int((self._s.clock.monotonic() - started) * 1000)
            await self._s.meter.record(
                uow,
                context,
                operation=OperationType.EMBEDDINGS,
                model=model,
                usage=usage,
                cost=cost,
                latency_ms=latency_ms,
                cached=not pending,
                metadata={"inputs": str(len(command.inputs)), "cache_hits": str(len(cached))},
            )
            await self._s.audit.record(
                uow,
                context,
                action="embeddings.create",
                resource=model.qualified,
                attributes={"inputs": len(command.inputs), "cache_hits": len(cached)},
            )
            await uow.commit()

        ordered = tuple(vectors[index] for index in sorted(vectors))
        return EmbeddingsResult(
            request_id=context.request_id,
            model=model,
            vectors=ordered,
            usage=usage,
            cost=cost,
            latency_ms=latency_ms,
            cache_hits=len(cached),
        )

    @staticmethod
    def _validate(command: EmbeddingsCommand) -> None:
        if not command.inputs:
            raise ValidationError("At least one input is required")
        if len(command.inputs) > _MAX_BATCH:
            raise ValidationError(
                "Embedding batch too large",
                details={"max": _MAX_BATCH, "actual": len(command.inputs)},
            )
        if any(not text.strip() for text in command.inputs):
            raise ValidationError("Embedding inputs must not be blank")


__all__ = ["EmbeddingsUseCase"]
