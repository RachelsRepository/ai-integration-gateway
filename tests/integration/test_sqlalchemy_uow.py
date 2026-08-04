"""Persistence unit-of-work tests (SQLite by default, Postgres when configured)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_gateway.domain.entities.tenant import ApiKey, QuotaPeriod, Role, Tenant
from ai_gateway.domain.entities.usage import OperationType, UsageRecord
from ai_gateway.domain.events import DomainEvent, EventType
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.persistence.models import Base
from ai_gateway.infrastructure.persistence.sqlalchemy import SqlAlchemyUnitOfWork


def _unique_tenant_id() -> TenantId:
    """Allocate a fresh tenant id so shared Postgres runs stay isolated."""
    return TenantId(str(uuid.uuid4()))


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Provide a session factory backed by SQLite or optional Postgres."""
    dsn = os.environ.get("AIGW_TEST_DB_DSN", "sqlite+aiosqlite:///:memory:")
    engine = create_async_engine(dsn)
    if dsn.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover - connection/driver failures
        await engine.dispose()
        pytest.skip(f"Database unavailable: {exc}")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_tenant_api_key_fk_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Tenant rows must exist before API keys (FK) even when flushed together."""
    tenant = Tenant(name="fk-order", id=_unique_tenant_id())
    prefix = f"sk-fk-{uuid.uuid4().hex[:8]}"
    api_key = ApiKey(
        tenant_id=tenant.id,
        prefix=prefix,
        hashed_secret="hashed-secret",
        roles=frozenset({Role.SERVICE}),
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.tenants.upsert(tenant)
        await uow.api_keys.add(api_key)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.tenants.get(tenant.id) is not None
        assert len(await uow.api_keys.find_by_prefix(prefix)) == 1
        await uow.rollback()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Persist and read back core aggregates through separate units of work."""
    tenant = Tenant(name="sql-test", id=_unique_tenant_id())
    prefix = f"sk-test-{uuid.uuid4().hex[:8]}"
    request_id = f"req-sql-{uuid.uuid4().hex[:12]}"
    api_key = ApiKey(
        tenant_id=tenant.id,
        prefix=prefix,
        hashed_secret="hashed-secret",
        roles=frozenset({Role.SERVICE}),
    )
    usage = UsageRecord(
        tenant_id=tenant.id,
        request_id=RequestId(request_id),
        operation=OperationType.CHAT,
        model=ModelRef(provider=ProviderName.ECHO, name="echo-1"),
        usage=TokenUsage(prompt_tokens=12, completion_tokens=8),
        cost=Money.from_micros(1500),
        latency_ms=42,
        occurred_at=datetime.now(UTC),
    )
    event = DomainEvent(
        type=EventType.USAGE_RECORDED,
        tenant_id=tenant.id,
        payload={"request_id": request_id},
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.tenants.upsert(tenant)
        await uow.api_keys.add(api_key)
        await uow.usage.record(usage)
        await uow.outbox.enqueue(event)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        loaded = await uow.tenants.get(tenant.id)
        assert loaded is not None
        assert loaded.name == "sql-test"
        keys = await uow.api_keys.find_by_prefix(prefix)
        assert len(keys) == 1
        snap = await uow.usage.snapshot(tenant.id, at=datetime.now(UTC))
        assert QuotaPeriod.DAILY in snap
        assert snap[QuotaPeriod.DAILY].tokens == 20
        pending = await uow.outbox.fetch_unpublished(limit=10)
        # Filter to this tenant — shared Postgres may retain other unpublished rows.
        mine = [item for item in pending if item[1].tenant_id == tenant.id]
        assert len(mine) == 1
        outbox_id, loaded_event = mine[0]
        assert loaded_event.type is EventType.USAGE_RECORDED
        await uow.outbox.mark_published([outbox_id], at=datetime.now(UTC))
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        pending = await uow.outbox.fetch_unpublished(limit=50)
        assert all(event.tenant_id != tenant.id for _, event in pending)
        await uow.rollback()
