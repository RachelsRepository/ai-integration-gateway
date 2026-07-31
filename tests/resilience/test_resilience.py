"""Resilience and failover tests."""

from __future__ import annotations

import pytest

from ai_gateway.application.ports.llm_provider import (
    ProviderCallContext,
    ProviderChatRequest,
    ProviderChatResponse,
)
from ai_gateway.application.services.execution import ProviderExecutor
from ai_gateway.domain.entities.message import Message
from ai_gateway.domain.errors import ProviderError
from ai_gateway.domain.policies.retry import RetryPolicy
from ai_gateway.domain.policies.routing import RoutingCandidate
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.infrastructure.clock import SystemClock
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog
from ai_gateway.infrastructure.providers.echo import EchoProvider
from ai_gateway.infrastructure.providers.registry import DefaultProviderRegistry
from ai_gateway.infrastructure.resilience.circuit_breaker import InMemoryCircuitBreakerRegistry
from ai_gateway.observability.metrics import NullMetrics


class FlakyProvider(EchoProvider):
    """Fails once, then succeeds."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> ProviderChatResponse:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("transient", provider=self.name.value)
        return await super().chat(request, context)


@pytest.mark.asyncio
async def test_executor_retries_transient_failure() -> None:
    flaky = FlakyProvider()
    catalog = StaticModelCatalog()
    registry = DefaultProviderRegistry({ProviderName.ECHO: flaky})
    breakers = InMemoryCircuitBreakerRegistry()
    executor = ProviderExecutor(
        providers=registry,
        breakers=breakers,
        clock=SystemClock(),
        metrics=NullMetrics(),
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.0, jitter=False),
    )
    spec = next(s for s in catalog.for_provider(ProviderName.ECHO) if "echo-1" in s.ref.name)
    chain = (RoutingCandidate(spec=spec, status=ProviderStatus.HEALTHY),)
    request = ProviderChatRequest(
        model=ModelRef(ProviderName.ECHO, "echo-1"),
        messages=(Message.user("retry"),),
    )
    context = ProviderCallContext(request_id=RequestId("r"), tenant_id=TenantId("t"))
    outcome = await executor.chat(chain, request, context)
    assert "retry" in outcome.value.content
    assert flaky.calls == 2
    assert outcome.total_attempts == 2
