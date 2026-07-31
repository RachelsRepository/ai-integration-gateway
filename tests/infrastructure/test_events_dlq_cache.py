"""Infrastructure adapter tests for events, DLQ, cache, and persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_gateway.application.ports.dlq import DeadLetterRecord
from ai_gateway.config.settings import DatabaseSettings
from ai_gateway.domain.entities.usage import UsageAggregate
from ai_gateway.domain.errors import DomainError
from ai_gateway.domain.events import DomainEvent, EventType
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.infrastructure.cache.redis_cache import (
    RedisCache,
    RedisLock,
    RedisLockManager,
    create_redis_client,
)
from ai_gateway.infrastructure.dlq.memory import InMemoryDeadLetterQueue
from ai_gateway.infrastructure.events.kafka_publisher import KafkaEventPublisher
from ai_gateway.infrastructure.events.memory import InMemoryEventPublisher
from ai_gateway.infrastructure.persistence import database as db_module
from ai_gateway.infrastructure.persistence import models as orm_models
from ai_gateway.infrastructure.persistence.memory import InMemoryUnitOfWork


@pytest.mark.asyncio
async def test_in_memory_event_publisher() -> None:
    publisher = InMemoryEventPublisher()
    await publisher.start()
    event = DomainEvent(type=EventType.USAGE_RECORDED, tenant_id=TenantId("t1"))
    await publisher.publish(event)
    await publisher.publish_batch([event])
    assert len(publisher.events) == 2
    publisher.clear()
    assert publisher.events == []
    await publisher.stop()
    assert publisher.started is False


@pytest.mark.asyncio
async def test_in_memory_dlq_lifecycle() -> None:
    dlq = InMemoryDeadLetterQueue()
    now = datetime.now(UTC)
    record = DeadLetterRecord(
        id="r1",
        kind="event_publish",
        payload={"x": 1},
        error="fail",
        attempts=1,
        tenant_id=TenantId("t1"),
        enqueued_at=now,
        next_attempt_at=now - timedelta(minutes=1),
    )
    await dlq.put(record)
    claimed = await dlq.claim(limit=10, now=now)
    assert len(claimed) == 1
    await dlq.reschedule("r1", next_attempt_at=now + timedelta(hours=1), error="again")
    assert await dlq.size() == 1
    await dlq.resolve("r1")
    assert await dlq.size() == 0


@pytest.mark.asyncio
async def test_kafka_publisher_disabled() -> None:
    publisher = KafkaEventPublisher(bootstrap_servers="localhost:9092", enabled=False)
    event = DomainEvent(type=EventType.AUDIT_LOGGED, tenant_id=TenantId("t1"))
    with pytest.raises(DomainError, match="disabled"):
        await publisher.publish(event)


@pytest.mark.asyncio
async def test_kafka_publisher_not_started() -> None:
    publisher = KafkaEventPublisher(bootstrap_servers="localhost:9092", enabled=True)
    event = DomainEvent(type=EventType.AUDIT_LOGGED, tenant_id=TenantId("t1"))
    with pytest.raises(DomainError, match="not been started"):
        await publisher.publish_batch([event])


@pytest.mark.asyncio
async def test_kafka_publisher_publish_batch() -> None:
    mock_producer = AsyncMock()
    mock_producer.start = AsyncMock()
    mock_producer.stop = AsyncMock()
    mock_producer.send_and_wait = AsyncMock()

    with patch("aiokafka.AIOKafkaProducer", return_value=mock_producer):
        publisher = KafkaEventPublisher(
            bootstrap_servers="localhost:9092", enabled=True, topic_prefix="prod."
        )
        await publisher.start()
        event = DomainEvent(type=EventType.USAGE_RECORDED, tenant_id=TenantId("t1"))
        await publisher.publish(event)
        await publisher.publish_batch([event])
        await publisher.stop()
        assert mock_producer.send_and_wait.await_count == 2


@pytest.mark.asyncio
async def test_redis_cache_and_lock() -> None:
    client = AsyncMock()
    client.get = AsyncMock(return_value=b"value")
    client.set = AsyncMock()
    client.delete = AsyncMock()
    client.incrby = AsyncMock(return_value=3)
    client.expire = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    client.eval = AsyncMock()

    cache = RedisCache(client)
    assert await cache.get("k") == b"value"
    await cache.set("k", b"v", ttl_seconds=60)
    await cache.set("k2", b"v")
    await cache.delete("k")
    assert await cache.incr("counter", amount=2, ttl_seconds=30) == 3
    assert await cache.ping() is True

    client.set = AsyncMock(side_effect=[True, False, False])
    lock = RedisLock(client, "resource", ttl_seconds=5, wait_seconds=0.1)
    assert await lock.__aenter__() is True
    await lock.__aexit__(None, None, None)

    manager = RedisLockManager(client)
    assert isinstance(manager.lock("x"), RedisLock)

    client.ping = AsyncMock(side_effect=RuntimeError("down"))
    assert await cache.ping() is False


def test_create_redis_client_failure() -> None:
    with patch(
        "ai_gateway.infrastructure.cache.redis_cache.Redis.from_url", side_effect=OSError("nope")
    ):
        with pytest.raises(DomainError):
            create_redis_client("redis://bad")


def test_orm_models_importable() -> None:
    assert orm_models.Base is not None
    assert hasattr(orm_models, "TenantModel")


def test_create_engine_and_session_factory() -> None:
    settings = DatabaseSettings(dsn="postgresql+asyncpg://u:p@localhost/db")
    engine = db_module.create_engine(settings)
    factory = db_module.create_session_factory(engine)
    assert engine is not None
    assert factory is not None


@pytest.mark.asyncio
async def test_session_scope_commits_and_rolls_back() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    factory = MagicMock(return_value=session)

    async with db_module.session_scope(factory):
        pass
    session.commit.assert_awaited_once()

    session.commit.reset_mock()
    session.rollback.reset_mock()
    with pytest.raises(RuntimeError):
        async with db_module.session_scope(factory):
            raise RuntimeError("fail")
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_memory_repositories_uncovered_paths(tenant: object) -> None:
    tenant_id = tenant.id  # type: ignore[attr-defined]
    async with InMemoryUnitOfWork() as uow:
        await uow.tenants.upsert(tenant)  # type: ignore[arg-type]
        await uow.commit()

    async with InMemoryUnitOfWork() as uow:
        tenants = await uow.tenants.list_active()
        assert tenants
        await uow.audit.purge_older_than(datetime.now(UTC) + timedelta(days=1), limit=1)
        await uow.commit()

    async with InMemoryUnitOfWork() as uow:
        await uow.usage.upsert_aggregate(
            UsageAggregate(tenant_id=tenant_id, period_key="2026-01-15", model="echo/echo-1")
        )
        await uow.commit()
