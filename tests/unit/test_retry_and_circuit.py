"""Retry policy and circuit breaker tests."""

from __future__ import annotations

import pytest

from ai_gateway.domain.errors import ProviderError
from ai_gateway.domain.policies.retry import RetryPolicy
from ai_gateway.infrastructure.resilience.circuit_breaker import InMemoryCircuitBreaker


def test_retry_policy_backoff_and_retryable() -> None:
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.1, jitter=False)
    assert policy.delay_for(1) == pytest.approx(0.1)
    assert policy.delay_for(2) == pytest.approx(0.2)
    assert policy.should_retry(ProviderError("boom", provider="echo"), attempt=1)
    assert not policy.should_retry(ProviderError("boom", provider="echo"), attempt=3)


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold() -> None:
    breaker = InMemoryCircuitBreaker(name="echo", failure_threshold=2, reset_timeout_seconds=60)
    assert await breaker.allows_request()
    await breaker.record_failure(error="x")
    await breaker.record_failure(error="x")
    assert breaker.snapshot().is_open
    assert not await breaker.allows_request()
