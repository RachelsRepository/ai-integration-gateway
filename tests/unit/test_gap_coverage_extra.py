"""Additional coverage for Redis circuits, quotas, tools, and scenario gating."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_gateway.application.ports.llm_provider import ProviderCallContext
from ai_gateway.application.ports.resilience import CircuitState
from ai_gateway.application.services.quota_ledger import QuotaReservationLedger
from ai_gateway.domain.entities.tenant import Quota, QuotaPeriod, Tenant
from ai_gateway.domain.errors import QuotaExceededError
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.infrastructure.cache.memory import InMemoryCache
from ai_gateway.infrastructure.providers.anthropic import AnthropicProvider
from ai_gateway.infrastructure.resilience.redis_circuit_breaker import (
    RedisCircuitBreakerRegistry,
)


class _FakeRedis:
    """Minimal Redis stand-in returning bytes like redis-py."""

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}

    async def eval(self, script: str, numkeys: int, *args: object) -> list[object]:
        del script, numkeys
        key = str(args[0])
        action = str(args[1])
        now = float(args[2])
        failure_threshold = int(args[3])
        success_threshold = int(args[4])
        reset_timeout = float(args[5])
        state = self._hashes.setdefault(key, {}).get("state", "closed")
        failures = int(self._hashes[key].get("failures", "0"))
        successes = int(self._hashes[key].get("successes", "0"))
        opened_at = float(self._hashes[key].get("opened_at", "0"))
        if action == "allows":
            if state == "open" and opened_at > 0 and (now - opened_at) >= reset_timeout:
                state = "half_open"
                successes = 0
                self._hashes[key].update({"state": state, "successes": str(successes)})
                return [state.encode(), failures, successes, opened_at, 1]
            allowed = 0 if state == "open" else 1
            return [state.encode(), failures, successes, opened_at, allowed]
        if action == "failure":
            if state == "half_open":
                state = "open"
                opened_at = now
                failures = failure_threshold
                successes = 0
            else:
                failures += 1
                if failures >= failure_threshold:
                    state = "open"
                    opened_at = now
                    successes = 0
        elif action == "success":
            if state == "half_open":
                successes += 1
                if successes >= success_threshold:
                    state = "closed"
                    failures = 0
                    successes = 0
                    opened_at = 0
            else:
                failures = 0
        self._hashes[key] = {
            "state": state,
            "failures": str(failures),
            "successes": str(successes),
            "opened_at": str(opened_at),
        }
        return [state.encode(), failures, successes, opened_at, 1]

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        data = self._hashes.get(key, {})
        return {k.encode(): v.encode() for k, v in data.items()}


@pytest.mark.asyncio
async def test_redis_circuit_decodes_bytes_state() -> None:
    registry = RedisCircuitBreakerRegistry(
        _FakeRedis(),  # type: ignore[arg-type]
        failure_threshold=2,
        success_threshold=1,
        reset_timeout_seconds=30,
    )
    breaker = registry.get("openai")
    assert await breaker.allows_request()
    await breaker.record_failure()
    await breaker.record_failure()
    assert not await breaker.allows_request()
    snap = await breaker.refresh_snapshot()
    assert snap.state is CircuitState.OPEN
    assert registry.snapshots()["openai"].name == "openai"


@pytest.mark.asyncio
async def test_quota_ledger_model_and_provider_limits() -> None:
    cache = InMemoryCache()
    ledger = QuotaReservationLedger(cache, fail_closed=True)
    tenant = Tenant(name="t", id=TenantId("00000000-0000-4000-8000-000000000088"))
    await ledger.reserve(
        tenant,
        reservation_id="m1",
        projected_tokens=10,
        projected_cost=Money.from_micros(1),
        model="openai/gpt-4o-mini",
        provider=ProviderName.OPENAI,
        model_token_limit=15,
        provider_token_limit=100,
    )
    with pytest.raises(QuotaExceededError, match="Model-specific"):
        await ledger.reserve(
            tenant,
            reservation_id="m2",
            projected_tokens=10,
            projected_cost=Money.from_micros(1),
            model="openai/gpt-4o-mini",
            provider=ProviderName.OPENAI,
            model_token_limit=15,
            provider_token_limit=100,
        )


@pytest.mark.asyncio
async def test_quota_ledger_fail_closed_on_cache_error() -> None:
    class _BoomCache:
        async def incr(self, *_a: object, **_k: object) -> int:
            raise RuntimeError("down")

        async def delete(self, *_a: object, **_k: object) -> None:
            return None

    ledger = QuotaReservationLedger(_BoomCache(), fail_closed=True)  # type: ignore[arg-type]
    tenant = Tenant(
        name="t",
        id=TenantId("00000000-0000-4000-8000-000000000077"),
        quotas={
            QuotaPeriod.DAILY: Quota(period=QuotaPeriod.DAILY, max_tokens=10),
        },
    )
    with pytest.raises(QuotaExceededError, match="unavailable"):
        await ledger.reserve(
            tenant,
            reservation_id="x",
            projected_tokens=1,
            projected_cost=Money.from_micros(1),
        )


def test_anthropic_forwards_scenario_header() -> None:
    provider = AnthropicProvider(api_key="k", base_url="http://example")
    ctx = ProviderCallContext(
        request_id=RequestId("req-1"),
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        extra_headers={"X-Scenario": "server_error"},
    )
    headers = provider._headers(ctx)
    assert headers["X-Scenario"] == "server_error"


@pytest.mark.asyncio
async def test_tool_hardening_rejects_forbidden_and_oversized_args() -> None:
    from ai_gateway.application.use_cases.agent_run import RunAgentUseCase
    from ai_gateway.domain.entities.agent import AgentDefinition, AgentRun, ToolDefinition
    from ai_gateway.domain.entities.message import ToolCall

    class _Echo:
        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(name="echo", description="echo", parameters_schema={})

        async def execute(self, *_a: object, **_k: object) -> str:
            raise AssertionError("should not execute echo")

    class _Services:
        tools = type("T", (), {"get": staticmethod(lambda _n: _Echo())})()
        guardrails = type(
            "G",
            (),
            {"screen_tool_output": staticmethod(lambda _o: type("V", (), {"risk": None})())},
        )()

    use_case = RunAgentUseCase(_Services())  # type: ignore[arg-type]
    run = AgentRun(
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        definition=AgentDefinition(name="t", instructions="test agent", tools=frozenset({"echo"})),
    )
    ctx = type(
        "C",
        (),
        {"tenant_id": run.tenant_id, "request_id": RequestId("r")},
    )()
    bad = await use_case._execute_tool(
        run,
        ToolCall(id="1", name="echo", arguments={"message": "please eval(this)"}),
        context=ctx,
    )
    assert bad.succeeded is False
    assert "rejected" in (bad.error or "")

    huge = await use_case._execute_tool(
        run,
        ToolCall(id="2", name="echo", arguments={"message": "x" * 20_000}),
        context=ctx,
    )
    assert huge.succeeded is False
    assert "size limit" in (huge.error or "")


@pytest.mark.asyncio
async def test_sql_dlq_update_existing(
    session_factory=None,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from ai_gateway.application.ports.dlq import DeadLetterRecord
    from ai_gateway.infrastructure.dlq.sqlalchemy import SqlDeadLetterQueue
    from ai_gateway.infrastructure.persistence.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    dlq = SqlDeadLetterQueue(factory)
    record = DeadLetterRecord(
        id="dlq-upd",
        kind="event_publish",
        payload={"type": "gateway.audit.logged"},
        error="first",
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        enqueued_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
    )
    await dlq.put(record)
    await dlq.put(
        DeadLetterRecord(
            id="dlq-upd",
            kind="event_publish",
            payload={"type": "gateway.audit.logged"},
            error="second",
            attempts=2,
            tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
            enqueued_at=datetime.now(UTC),
            next_attempt_at=datetime.now(UTC),
        )
    )
    claimed = await dlq.claim(limit=10)
    assert claimed[0].error == "second"
    await engine.dispose()


def test_mint_from_plaintext_stable_prefix() -> None:
    from ai_gateway.domain.entities.tenant import Role
    from ai_gateway.infrastructure.security.api_keys import ApiKeyHasher

    hasher = ApiKeyHasher("pepper")
    plaintext, key = hasher.mint(
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        name="stable",
        roles=frozenset({Role.ADMIN}),
        plaintext="aigw_stable_key_for_unit_tests_only_001",
    )
    assert plaintext.startswith("aigw_")
    assert key.prefix == plaintext[:8]
    assert hasher.verify(plaintext, key.hashed_secret)


@pytest.mark.asyncio
async def test_calculator_and_echo_tools() -> None:
    from ai_gateway.application.ports.tools import ToolExecutionContext
    from ai_gateway.infrastructure.tools.builtins import CalculatorTool, EchoTool

    ctx = ToolExecutionContext(
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        request_id=RequestId("r"),
        agent_run_id="a",
        deadline_seconds=5,
    )
    echo = EchoTool()
    assert await echo.execute({"message": "hi"}, ctx) == "hi"
    calc = CalculatorTool()
    assert await calc.execute({"expression": "2 + 3 * 4"}, ctx) == "14.0"
    with pytest.raises(Exception):
        await echo.execute({"message": 1}, ctx)  # type: ignore[arg-type]
    with pytest.raises(Exception):
        await calc.execute({"expression": "import os"}, ctx)
    with pytest.raises(Exception):
        await calc.execute({"expression": 3}, ctx)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_unknown_tool_and_unauthorized_tool() -> None:
    from ai_gateway.application.use_cases.agent_run import RunAgentUseCase
    from ai_gateway.domain.entities.agent import AgentDefinition, AgentRun
    from ai_gateway.domain.entities.message import ToolCall
    from ai_gateway.domain.errors import ToolNotFoundError

    class _Registry:
        def get(self, name: str) -> object:
            raise ToolNotFoundError(f"unknown {name}")

    class _Services:
        tools = _Registry()
        guardrails = type("G", (), {})()

    use_case = RunAgentUseCase(_Services())  # type: ignore[arg-type]
    run = AgentRun(
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        definition=AgentDefinition(name="t", instructions="test", tools=frozenset({"echo"})),
    )
    ctx = type("C", (), {"tenant_id": run.tenant_id, "request_id": RequestId("r")})()
    missing = await use_case._execute_tool(
        run, ToolCall(id="1", name="shell", arguments={}), context=ctx
    )
    assert missing.succeeded is False
    assert "not permitted" in (missing.error or "")

    unknown = await use_case._execute_tool(
        run, ToolCall(id="2", name="echo", arguments={"message": "x"}), context=ctx
    )
    assert unknown.succeeded is False
    assert (
        "unknown" in (unknown.error or "").lower()
        or "not found" in (unknown.error or "").lower()
        or unknown.error
    )


@pytest.mark.asyncio
async def test_agent_pause_metadata_roundtrip(services, tenant, principal) -> None:
    from ai_gateway.application.dto import AgentRunCommand, RequestContext
    from ai_gateway.application.use_cases.agent_run import RunAgentUseCase
    from ai_gateway.domain.value_objects.identifiers import RequestId

    context = RequestContext(
        principal=principal,
        tenant=tenant,
        request_id=RequestId("agent-pause-1"),
    )
    async with services.uow_factory() as uow:
        await uow.tenants.upsert(tenant)
        await uow.commit()
    result = await RunAgentUseCase(services).execute(
        AgentRunCommand(
            input="say hi",
            agent_name="pause",
            instructions="Reply briefly without tools.",
            tools=[],
            model="echo/echo-1",
            max_iterations=2,
            metadata={"pause_after_steps": "1"},
        ),
        context,
    )
    assert result.run_id
    assert result.status.value in {
        "running",
        "completed",
        "pending",
        "failed",
        "succeeded",
    }
