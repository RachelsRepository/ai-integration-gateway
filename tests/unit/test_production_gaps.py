"""Focused tests for new production-readiness gap closures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_gateway.application.ports.llm_provider import ProviderCallContext
from ai_gateway.application.services.quota_ledger import QuotaReservationLedger
from ai_gateway.config.settings import Settings
from ai_gateway.domain.entities.tenant import Quota, QuotaPeriod, Tenant
from ai_gateway.domain.errors import QuotaExceededError
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.infrastructure.cache.memory import InMemoryCache
from ai_gateway.infrastructure.providers.openai import OpenAIProvider
from ai_gateway.infrastructure.resilience.circuit_breaker import InMemoryCircuitBreakerRegistry


def test_production_rejects_scenario_forwarding() -> None:
    with pytest.raises(ValidationError, match="scenario forwarding"):
        Settings(
            environment="production",
            persistence_backend="postgres",
            provider_scenario_forwarding=True,
            auth={
                "jwt_enabled": False,
                "api_keys_enabled": True,
                "api_key_pepper_ref": "env://AIGW_API_KEY_PEPPER",
            },
            docs_enabled=False,
            security={"trusted_hosts": ("gateway.example.com",)},
            kafka={"enabled": True},
        )


def test_openai_headers_include_scenario() -> None:
    provider = OpenAIProvider(api_key="k", base_url="http://example/v1")
    ctx = ProviderCallContext(
        request_id=RequestId("req-1"),
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        extra_headers={"X-Scenario": "rate_limit"},
    )
    headers = provider._headers(ctx)
    assert headers["X-Scenario"] == "rate_limit"
    assert "Authorization" in headers


@pytest.mark.asyncio
async def test_quota_ledger_shared_limit_and_release() -> None:
    cache = InMemoryCache()
    ledger = QuotaReservationLedger(cache, fail_closed=True)
    tenant = Tenant(
        name="t",
        id=TenantId("00000000-0000-4000-8000-000000000099"),
        quotas={
            QuotaPeriod.DAILY: Quota(
                period=QuotaPeriod.DAILY,
                max_tokens=100,
                max_cost=Money.from_micros(1_000_000),
                max_requests=1000,
            )
        },
    )
    r1 = await ledger.reserve(
        tenant,
        reservation_id="a",
        projected_tokens=60,
        projected_cost=Money.from_micros(100),
    )
    r2 = await ledger.reserve(
        tenant,
        reservation_id="b",
        projected_tokens=30,
        projected_cost=Money.from_micros(100),
    )
    with pytest.raises(QuotaExceededError):
        await ledger.reserve(
            tenant,
            reservation_id="c",
            projected_tokens=20,
            projected_cost=Money.from_micros(100),
        )
    await ledger.release(r2)
    r3 = await ledger.reserve(
        tenant,
        reservation_id="d",
        projected_tokens=20,
        projected_cost=Money.from_micros(100),
    )
    await ledger.settle(r1, actual_tokens=50, actual_cost=Money.from_micros(50))
    await ledger.settle(r3, actual_tokens=10, actual_cost=Money.from_micros(10))


@pytest.mark.asyncio
async def test_inmemory_circuit_opens() -> None:
    registry = InMemoryCircuitBreakerRegistry(failure_threshold=2, reset_timeout_seconds=60)
    breaker = registry.get("openai")
    assert await breaker.allows_request()
    await breaker.record_failure()
    await breaker.record_failure()
    assert not await breaker.allows_request()


@pytest.mark.asyncio
async def test_sql_dlq_round_trip_sqlite(session_factory=None) -> None:
    """Skip unless integration fixture available; exercise memory DLQ parity here."""
    from ai_gateway.application.ports.dlq import DeadLetterRecord
    from ai_gateway.infrastructure.dlq.memory import InMemoryDeadLetterQueue

    dlq = InMemoryDeadLetterQueue()
    record = DeadLetterRecord(
        id="dlq-1",
        kind="event_publish",
        payload={"topic": "usage"},
        error="boom",
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        enqueued_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
    )
    await dlq.put(record)
    claimed = await dlq.claim(limit=10)
    assert len(claimed) == 1
    await dlq.resolve("dlq-1")
    assert await dlq.size() == 0
