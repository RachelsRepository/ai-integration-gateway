"""Worker job and runner tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from ai_gateway.application.ports.dlq import DeadLetterRecord
from ai_gateway.domain.entities.conversation import Conversation
from ai_gateway.domain.entities.usage import OperationType, UsageRecord
from ai_gateway.domain.events import DomainEvent, EventType
from ai_gateway.domain.value_objects.identifiers import ConversationId, RequestId, TenantId
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.clock import SystemClock
from ai_gateway.infrastructure.dlq.memory import InMemoryDeadLetterQueue
from ai_gateway.infrastructure.events.memory import InMemoryEventPublisher
from ai_gateway.infrastructure.persistence.memory import InMemoryUnitOfWork
from ai_gateway.infrastructure.resilience.circuit_breaker import InMemoryCircuitBreakerRegistry
from ai_gateway.observability.metrics import NullMetrics
from ai_gateway.workers.jobs import (
    ConversationCleanupJob,
    DlqReplayJob,
    OutboxRelayJob,
    TelemetryExportJob,
    UsageAggregationJob,
)
from ai_gateway.workers.runner import WorkerRunner


def _usage_record(tenant_id: TenantId) -> UsageRecord:
    return UsageRecord(
        tenant_id=tenant_id,
        request_id=RequestId("req-1"),
        operation=OperationType.CHAT,
        model=ModelRef(ProviderName.ECHO, "echo-1"),
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
        cost=Money.of("0.001"),
        latency_ms=100,
        occurred_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_usage_aggregation_job(tenant: object) -> None:
    tenant_id = tenant.id  # type: ignore[attr-defined]
    async with InMemoryUnitOfWork() as uow:
        await uow.usage.record(_usage_record(tenant_id))
        await uow.commit()

    job = UsageAggregationJob(InMemoryUnitOfWork, batch_size=100)
    count = await job.run()
    assert count == 1

    async with InMemoryUnitOfWork() as uow:
        aggregates = await uow.usage.aggregates_for(
            tenant_id, since=datetime(2026, 1, 1).date(), until=datetime(2026, 1, 31).date()
        )
    assert len(aggregates) >= 2


@pytest.mark.asyncio
async def test_usage_aggregation_empty() -> None:
    assert await UsageAggregationJob(InMemoryUnitOfWork).run() == 0


@pytest.mark.asyncio
async def test_conversation_cleanup_job(tenant: object) -> None:
    tenant_id = tenant.id  # type: ignore[attr-defined]
    clock = SystemClock()
    stale = Conversation(
        tenant_id=tenant_id,
        id=ConversationId("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        updated_at=clock.now() - timedelta(days=60),
    )
    async with InMemoryUnitOfWork() as uow:
        await uow.conversations.save(stale)
        await uow.commit()

    job = ConversationCleanupJob(InMemoryUnitOfWork, clock, retention_days=30)
    deleted = await job.run()
    assert deleted == 1


@pytest.mark.asyncio
async def test_outbox_relay_success(tenant: object) -> None:
    tenant_id = tenant.id  # type: ignore[attr-defined]
    event = DomainEvent(type=EventType.USAGE_RECORDED, tenant_id=tenant_id, payload={"n": 1})
    async with InMemoryUnitOfWork() as uow:
        await uow.outbox.enqueue(event)
        await uow.commit()

    publisher = InMemoryEventPublisher()
    dlq = InMemoryDeadLetterQueue()
    job = OutboxRelayJob(InMemoryUnitOfWork, publisher, SystemClock(), dlq)
    published = await job.run()
    assert published == 1
    assert len(publisher.events) == 1


@pytest.mark.asyncio
async def test_outbox_relay_failure_moves_to_dlq(tenant: object) -> None:
    tenant_id = tenant.id  # type: ignore[attr-defined]
    event = DomainEvent(type=EventType.AUDIT_LOGGED, tenant_id=tenant_id)
    async with InMemoryUnitOfWork() as uow:
        await uow.outbox.enqueue(event)
        await uow.commit()

    publisher = AsyncMock()
    publisher.publish = AsyncMock(side_effect=RuntimeError("kafka down"))
    dlq = InMemoryDeadLetterQueue()
    job = OutboxRelayJob(
        InMemoryUnitOfWork, publisher, SystemClock(), dlq, max_attempts=1, batch_size=10
    )
    await job.run()
    assert await dlq.size() == 1


@pytest.mark.asyncio
async def test_dlq_replay_resolves_event_publish(tenant: object) -> None:
    tenant_id = tenant.id  # type: ignore[attr-defined]
    clock = SystemClock()
    dlq = InMemoryDeadLetterQueue()
    event = DomainEvent(type=EventType.TOOL_EXECUTED, tenant_id=tenant_id, payload={"tool": "x"})
    await dlq.put(
        DeadLetterRecord(
            id="dlq-1",
            kind="event_publish",
            payload=event.to_dict(),
            error="failed",
            attempts=1,
            tenant_id=tenant_id,
            enqueued_at=clock.now(),
            next_attempt_at=clock.now() - timedelta(minutes=1),
        )
    )
    publisher = InMemoryEventPublisher()
    metrics = NullMetrics()
    resolved = await DlqReplayJob(dlq, publisher, clock, metrics).run()
    assert resolved == 1
    assert await dlq.size() == 0
    assert len(publisher.events) == 1


@pytest.mark.asyncio
async def test_dlq_replay_reschedules_on_failure(tenant: object) -> None:
    tenant_id = tenant.id  # type: ignore[attr-defined]
    clock = SystemClock()
    dlq = InMemoryDeadLetterQueue()
    await dlq.put(
        DeadLetterRecord(
            id="dlq-2",
            kind="event_publish",
            payload={"type": "bad.type", "tenant_id": str(tenant_id), "payload": {}},
            error="failed",
            attempts=1,
            tenant_id=tenant_id,
            enqueued_at=clock.now(),
            next_attempt_at=clock.now() - timedelta(minutes=1),
        )
    )
    publisher = InMemoryEventPublisher()
    resolved = await DlqReplayJob(dlq, publisher, clock, NullMetrics()).run()
    assert resolved == 0
    assert await dlq.size() == 1


@pytest.mark.asyncio
async def test_telemetry_export_job() -> None:
    breakers = InMemoryCircuitBreakerRegistry(failure_threshold=2, reset_timeout_seconds=1.0)
    metrics = NullMetrics()
    count = await TelemetryExportJob(breakers, metrics).run()
    assert count >= 0


@pytest.mark.asyncio
async def test_worker_runner_schedules_and_stops() -> None:
    calls: list[int] = []

    async def tick() -> int:
        calls.append(1)
        return 1

    runner = WorkerRunner()
    runner.schedule("test", tick, interval_seconds=0.01)
    await asyncio.sleep(0.05)
    await runner.stop()
    assert calls


@pytest.mark.asyncio
async def test_worker_runner_handles_job_errors() -> None:
    async def boom() -> int:
        raise ValueError("boom")

    runner = WorkerRunner()
    runner.schedule("fail", boom, interval_seconds=0.01)
    await asyncio.sleep(0.03)
    await runner.stop()
