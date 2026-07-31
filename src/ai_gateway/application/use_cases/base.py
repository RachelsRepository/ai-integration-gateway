"""Shared collaborators and helpers for use cases."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ai_gateway.application.dto import RequestContext
from ai_gateway.application.ports.clock import Clock
from ai_gateway.application.ports.llm_provider import ProviderCallContext
from ai_gateway.application.ports.metrics import MetricsRecorder
from ai_gateway.application.ports.model_catalog import ModelCatalog
from ai_gateway.application.ports.provider_registry import ProviderRegistry
from ai_gateway.application.ports.repositories import UnitOfWork
from ai_gateway.application.ports.resilience import CircuitBreakerRegistry
from ai_gateway.application.ports.tools import ToolRegistry
from ai_gateway.application.services.audit import AuditTrail
from ai_gateway.application.services.caching import EmbeddingCache, ResponseCache
from ai_gateway.application.services.execution import ProviderExecutor
from ai_gateway.application.services.guardrails import GuardrailService
from ai_gateway.application.services.metering import UsageMeter
from ai_gateway.application.services.router import ModelRouter
from ai_gateway.domain.entities.message import Message
from ai_gateway.domain.entities.prompt import PromptTemplate
from ai_gateway.domain.errors import NotFoundError, ValidationError
from ai_gateway.domain.services.token_estimation import TokenEstimator
from ai_gateway.domain.value_objects.identifiers import TenantId

UnitOfWorkFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class GatewayServices:
    """The collaborators every request-serving use case needs.

    Grouping them keeps use-case constructors small and makes the composition root the
    single place where adapters are chosen.

    Attributes:
        uow_factory: Creates a fresh unit of work per request.
        router: Model routing service.
        executor: Resilient provider executor.
        meter: Usage metering and quota enforcement.
        guardrails: Input screening and output filtering.
        audit: Audit trail writer.
        response_cache: Chat completion cache.
        embedding_cache: Embedding vector cache.
        catalog: Model catalogue.
        providers: Provider registry.
        breakers: Circuit breaker registry.
        estimator: Token estimator used for pre-flight checks.
        clock: Injected clock.
        metrics: Metrics sink.
        tools: Tool registry available to agents.
        default_timeout_seconds: Per-provider-call deadline.
    """

    uow_factory: UnitOfWorkFactory
    router: ModelRouter
    executor: ProviderExecutor
    meter: UsageMeter
    guardrails: GuardrailService
    audit: AuditTrail
    response_cache: ResponseCache
    embedding_cache: EmbeddingCache
    catalog: ModelCatalog
    providers: ProviderRegistry
    breakers: CircuitBreakerRegistry
    estimator: TokenEstimator
    clock: Clock
    metrics: MetricsRecorder
    tools: ToolRegistry
    default_timeout_seconds: float = 60.0

    def call_context(
        self, context: RequestContext, *, timeout: float | None = None
    ) -> ProviderCallContext:
        """Build the provider call context for a request.

        Args:
            context: Inbound request context.
            timeout: Override for the per-call deadline.

        Returns:
            A populated :class:`ProviderCallContext`.
        """
        return ProviderCallContext(
            request_id=context.request_id,
            tenant_id=context.tenant_id,
            timeout_seconds=timeout or min(self.default_timeout_seconds, context.deadline_seconds),
            idempotency_key=context.idempotency_key,
            trace_id=context.trace_id,
        )


async def load_prompt(uow: UnitOfWork, tenant_id: TenantId, name: str) -> PromptTemplate:
    """Fetch a managed prompt or fail.

    Args:
        uow: Open unit of work.
        tenant_id: Owning tenant.
        name: Prompt name.

    Returns:
        The prompt template.

    Raises:
        NotFoundError: If the tenant has no prompt with that name.
    """
    prompt = await uow.prompts.get_by_name(tenant_id, name)
    if prompt is None:
        raise NotFoundError("Prompt not found", details={"prompt": name})
    return prompt


def require_messages(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Validate that a request carries at least one message.

    Args:
        messages: Transcript supplied by the caller.

    Returns:
        The same transcript.

    Raises:
        ValidationError: If the transcript is empty.
    """
    if not messages:
        raise ValidationError("At least one message or a prompt reference is required")
    return messages


__all__ = ["GatewayServices", "UnitOfWorkFactory", "load_prompt", "require_messages"]
