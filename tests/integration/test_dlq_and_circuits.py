"""Integration coverage for durable DLQ and Redis circuit breakers."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_gateway.application.ports.dlq import DeadLetterRecord
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.infrastructure.dlq.sqlalchemy import SqlDeadLetterQueue
from ai_gateway.infrastructure.persistence.models import Base
from ai_gateway.infrastructure.resilience.redis_circuit_breaker import RedisCircuitBreakerRegistry


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    dsn = os.environ.get("AIGW_TEST_DB_DSN", "sqlite+aiosqlite:///:memory:")
    engine = create_async_engine(dsn)
    if dsn.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _fk(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_sql_dlq_put_claim_resolve(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dlq = SqlDeadLetterQueue(session_factory)
    record = DeadLetterRecord(
        id="dlq-int-1",
        kind="event_publish",
        payload={"topic": "usage.recorded"},
        error="timeout",
        attempts=2,
        tenant_id=TenantId("00000000-0000-4000-8000-000000000001"),
        enqueued_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
        metadata={"source": "test"},
    )
    await dlq.put(record)
    assert await dlq.size() == 1
    claimed = await dlq.claim(limit=10)
    assert len(claimed) == 1
    assert claimed[0].id == "dlq-int-1"
    await dlq.reschedule("dlq-int-1", next_attempt_at=datetime.now(UTC), error="retry")
    await dlq.resolve("dlq-int-1")
    assert await dlq.size() == 0


@pytest.mark.asyncio
async def test_redis_circuit_breaker_falls_back_locally() -> None:
    class _Boom:
        async def eval(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("redis down")

        async def hgetall(self, *_args: object, **_kwargs: object) -> dict[str, str]:
            raise RuntimeError("redis down")

    registry = RedisCircuitBreakerRegistry(
        _Boom(),  # type: ignore[arg-type]
        failure_threshold=2,
        reset_timeout_seconds=30,
    )
    breaker = registry.get("openai")
    assert await breaker.allows_request()
    await breaker.record_failure(error="x")
    await breaker.record_failure(error="y")
    assert not await breaker.allows_request()
    snap = await breaker.refresh_snapshot()
    assert snap.name == "openai"
