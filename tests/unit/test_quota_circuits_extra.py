"""Extra coverage for quota ledger, metering reservation helpers, and Redis CB."""

from __future__ import annotations

import pytest

from ai_gateway.application.services.metering import UsageMeter
from ai_gateway.application.services.quota_ledger import QuotaReservationLedger
from ai_gateway.domain.entities.tenant import Quota, QuotaPeriod, Tenant
from ai_gateway.domain.errors import QuotaExceededError
from ai_gateway.domain.services.cost import CostCalculator
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.cache.memory import InMemoryCache
from ai_gateway.infrastructure.clock import SystemClock
from ai_gateway.infrastructure.rate_limiting.token_bucket import TokenBucketRateLimiter
from ai_gateway.infrastructure.resilience.redis_circuit_breaker import RedisCircuitBreakerRegistry
from ai_gateway.observability.metrics import NullMetrics

_RedisArg = str | bytes | int | float


def _require_float(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric, got bool")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (str, bytes, bytearray)):
        return float(value)
    raise TypeError(f"{label} must be float-compatible, got {type(value).__name__}: {value!r}")


def _require_int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (str, bytes, bytearray)):
        return int(value)
    raise TypeError(f"{label} must be int-compatible, got {type(value).__name__}: {value!r}")


class _FakeRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}

    async def eval(self, script: str, numkeys: int, *args: _RedisArg) -> list[object]:
        del script, numkeys
        key = str(args[0])
        action = str(args[1])
        now = _require_float(args[2], label="now")
        failure_threshold = _require_int(args[3], label="failure_threshold")
        success_threshold = _require_int(args[4], label="success_threshold")
        reset_timeout = _require_float(args[5], label="reset_timeout")
        state = self._hashes.setdefault(
            key, {"state": "closed", "failures": "0", "successes": "0", "opened_at": "0"}
        )
        failures = int(state["failures"])
        successes = int(state["successes"])
        opened_at = float(state["opened_at"])
        cur = state["state"]
        if action == "allows":
            if cur == "closed":
                return [cur, failures, successes, opened_at, 1]
            if cur == "open":
                if opened_at > 0 and (now - opened_at) >= reset_timeout:
                    state["state"] = "half_open"
                    state["successes"] = "0"
                    return ["half_open", failures, 0, opened_at, 1]
                return [cur, failures, successes, opened_at, 0]
            return [cur, failures, successes, opened_at, 1]
        if action == "success":
            if cur == "half_open":
                successes += 1
                if successes >= success_threshold:
                    cur, failures, successes, opened_at = "closed", 0, 0, 0.0
            else:
                failures = 0
        elif action == "failure":
            if cur == "half_open":
                cur, opened_at, successes, failures = "open", now, 0, failure_threshold
            else:
                failures += 1
                if failures >= failure_threshold:
                    cur, opened_at, successes = "open", now, 0
        state.update(
            {
                "state": cur,
                "failures": str(failures),
                "successes": str(successes),
                "opened_at": str(opened_at),
            }
        )
        return [cur, failures, successes, opened_at, 1]

    async def hgetall(self, name: str | bytes) -> dict[bytes, bytes]:
        key = name.decode() if isinstance(name, bytes) else name
        data = self._hashes.get(key, {})
        return {k.encode(): v.encode() for k, v in data.items()}


@pytest.mark.asyncio
async def test_redis_circuit_breaker_shared_state() -> None:
    client = _FakeRedis()
    registry = RedisCircuitBreakerRegistry(client, failure_threshold=2, reset_timeout_seconds=60)
    a = registry.get("openai")
    b = registry.get("openai")
    assert await a.allows_request()
    await a.record_failure()
    await a.record_failure()
    assert not await b.allows_request()
    snap = await a.refresh_snapshot()
    assert snap.state.value == "open"


@pytest.mark.asyncio
async def test_meter_reserve_settle_release() -> None:
    cache = InMemoryCache()
    meter = UsageMeter(
        rate_limiter=TokenBucketRateLimiter(cache),
        cost_calculator=CostCalculator({}),
        clock=SystemClock(),
        metrics=NullMetrics(),
        reservation_ledger=QuotaReservationLedger(cache, fail_closed=True),
    )
    tenant = Tenant(
        name="meter",
        id=TenantId("00000000-0000-4000-8000-000000000077"),
        quotas={
            QuotaPeriod.DAILY: Quota(
                period=QuotaPeriod.DAILY,
                max_tokens=500,
                max_cost=Money.from_micros(10_000_000),
            )
        },
    )
    model = ModelRef(provider=ProviderName.ECHO, name="echo-1")
    reservation = await meter.reserve(
        tenant,
        reservation_id="r1",
        projected_tokens=10,
        projected_cost=Money.from_micros(100),
        model=model,
    )
    assert reservation is not None
    await meter.settle_reservation(reservation, actual_tokens=8, actual_cost=Money.from_micros(80))
    reservation2 = await meter.reserve(
        tenant,
        reservation_id="r2",
        projected_tokens=10,
        projected_cost=Money.from_micros(100),
        model=model,
    )
    await meter.release_reservation(reservation2)


@pytest.mark.asyncio
async def test_quota_ledger_concurrency_limit() -> None:
    cache = InMemoryCache()
    ledger = QuotaReservationLedger(cache, fail_closed=True)
    tenant = Tenant(name="c", id=TenantId("00000000-0000-4000-8000-000000000088"))
    await ledger.reserve(
        tenant,
        reservation_id="1",
        projected_tokens=1,
        projected_cost=Money.zero(),
        concurrency_limit=1,
    )
    with pytest.raises(QuotaExceededError):
        await ledger.reserve(
            tenant,
            reservation_id="2",
            projected_tokens=1,
            projected_cost=Money.zero(),
            concurrency_limit=1,
        )


def test_usage_meter_price_and_project() -> None:
    from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog

    catalog = StaticModelCatalog()
    meter = UsageMeter(
        rate_limiter=TokenBucketRateLimiter(InMemoryCache()),
        cost_calculator=CostCalculator(catalog.price_book()),
        clock=SystemClock(),
        metrics=NullMetrics(),
    )
    model = ModelRef(provider=ProviderName.ECHO, name="echo-1")
    cost = meter.price(model, TokenUsage(prompt_tokens=1, completion_tokens=1))
    assert cost.micros >= 0
    projected = meter.project(model, prompt_tokens=10, max_output_tokens=5)
    assert projected.micros >= 0
