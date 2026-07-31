"""Background job implementations."""

from __future__ import annotations

from datetime import timedelta

from ai_gateway.application.ports.clock import Clock
from ai_gateway.application.ports.dlq import DeadLetterQueue, DeadLetterRecord
from ai_gateway.application.ports.events import EventPublisher
from ai_gateway.application.ports.metrics import MetricsRecorder
from ai_gateway.domain.entities.usage import UsageAggregate
from ai_gateway.domain.value_objects.identifiers import new_id
from ai_gateway.observability.logging import get_logger

logger = get_logger(__name__)


class UsageAggregationJob:
    """Rolls up unaggregated usage records into period buckets."""

    def __init__(self, uow_factory: type | object, *, batch_size: int = 500) -> None:
        """Initialise the job.

        Args:
            uow_factory: Callable returning a unit of work.
            batch_size: Maximum records per pass.
        """
        self._uow_factory = uow_factory  # type: ignore[assignment]
        self._batch_size = batch_size

    async def run(self) -> int:
        """Aggregate pending usage records.

        Returns:
            The number of records aggregated.
        """
        async with self._uow_factory() as uow:  # type: ignore[operator]
            records = await uow.usage.unaggregated(limit=self._batch_size)
            if not records:
                return 0
            buckets: dict[str, UsageAggregate] = {}
            for record in records:
                for period_key in (record.usage_date.isoformat(), record.billing_month):
                    for model_key in (record.model.qualified, "*"):
                        key = f"{record.tenant_id}:{period_key}:{model_key}"
                        aggregate = buckets.get(key)
                        if aggregate is None:
                            aggregate = UsageAggregate(
                                tenant_id=record.tenant_id,
                                period_key=period_key,
                                model=model_key,
                            )
                            buckets[key] = aggregate
                        aggregate.accumulate(record)
            for aggregate in buckets.values():
                await uow.usage.upsert_aggregate(aggregate)
            await uow.usage.mark_aggregated([record.id for record in records])
            await uow.commit()
            logger.info("usage_aggregated", count=len(records), buckets=len(buckets))
            return len(records)


class ConversationCleanupJob:
    """Deletes conversations that have exceeded the retention window."""

    def __init__(
        self,
        uow_factory: object,
        clock: Clock,
        *,
        retention_days: int = 30,
        batch_size: int = 500,
    ) -> None:
        """Initialise the job."""
        self._uow_factory = uow_factory
        self._clock = clock
        self._retention = timedelta(days=retention_days)
        self._batch_size = batch_size

    async def run(self) -> int:
        """Delete stale conversations.

        Returns:
            The number of conversations deleted.
        """
        cutoff = self._clock.now() - self._retention
        async with self._uow_factory() as uow:  # type: ignore[operator]
            deleted = await uow.conversations.delete_stale(
                older_than=cutoff, limit=self._batch_size
            )
            await uow.commit()
            logger.info("conversations_cleaned", deleted=deleted, cutoff=cutoff.isoformat())
            return int(deleted)


class OutboxRelayJob:
    """Publishes staged outbox events to the event bus."""

    def __init__(
        self,
        uow_factory: object,
        publisher: EventPublisher,
        clock: Clock,
        dlq: DeadLetterQueue,
        *,
        batch_size: int = 200,
        max_attempts: int = 5,
    ) -> None:
        """Initialise the job."""
        self._uow_factory = uow_factory
        self._publisher = publisher
        self._clock = clock
        self._dlq = dlq
        self._batch_size = batch_size
        self._max_attempts = max_attempts

    async def run(self) -> int:
        """Publish a batch of outbox events.

        Returns:
            The number of events successfully published.
        """
        async with self._uow_factory() as uow:  # type: ignore[operator]
            pending = await uow.outbox.fetch_unpublished(limit=self._batch_size)
        published: list[str] = []
        for outbox_id, event in pending:
            try:
                await self._publisher.publish(event)
                published.append(outbox_id)
            except Exception as exc:
                async with self._uow_factory() as uow:  # type: ignore[operator]
                    attempts = await uow.outbox.mark_failed(outbox_id, error=str(exc))
                    await uow.commit()
                if attempts >= self._max_attempts:
                    await self._dlq.put(
                        DeadLetterRecord(
                            id=new_id(),
                            kind="event_publish",
                            payload=event.to_dict(),
                            error=str(exc),
                            attempts=attempts,
                            tenant_id=event.tenant_id,
                            enqueued_at=self._clock.now(),
                            next_attempt_at=self._clock.now() + timedelta(minutes=5),
                        )
                    )
                    logger.error("outbox_moved_to_dlq", outbox_id=outbox_id, error=str(exc))
        if published:
            async with self._uow_factory() as uow:  # type: ignore[operator]
                await uow.outbox.mark_published(published, at=self._clock.now())
                await uow.commit()
        logger.info("outbox_relayed", published=len(published), pending=len(pending))
        return len(published)


class DlqReplayJob:
    """Attempts to replay dead-lettered work items."""

    def __init__(
        self,
        dlq: DeadLetterQueue,
        publisher: EventPublisher,
        clock: Clock,
        metrics: MetricsRecorder,
        *,
        batch_size: int = 50,
    ) -> None:
        """Initialise the job."""
        self._dlq = dlq
        self._publisher = publisher
        self._clock = clock
        self._metrics = metrics
        self._batch_size = batch_size

    async def run(self) -> int:
        """Replay due dead-letter records.

        Returns:
            The number of records successfully resolved.
        """
        claimed = await self._dlq.claim(limit=self._batch_size, now=self._clock.now())
        resolved = 0
        for record in claimed:
            try:
                if record.kind == "event_publish":
                    from ai_gateway.domain.events import DomainEvent, EventType
                    from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId

                    payload = record.payload
                    event = DomainEvent(
                        type=EventType(payload["type"]),
                        tenant_id=TenantId(payload["tenant_id"]),
                        payload=payload.get("payload") or {},
                        id=payload.get("id") or new_id(),
                        request_id=(
                            RequestId(payload["request_id"]) if payload.get("request_id") else None
                        ),
                    )
                    await self._publisher.publish(event)
                await self._dlq.resolve(record.id)
                resolved += 1
            except Exception as exc:
                await self._dlq.reschedule(
                    record.id,
                    next_attempt_at=self._clock.now() + timedelta(minutes=10),
                    error=str(exc),
                )
        depth = await self._dlq.size()
        self._metrics.set_gauge("gateway_dlq_depth", float(depth))
        logger.info("dlq_replayed", resolved=resolved, claimed=len(claimed), depth=depth)
        return resolved


class TelemetryExportJob:
    """Exports circuit breaker gauges for scrape-based metrics systems."""

    def __init__(self, breakers: object, metrics: MetricsRecorder) -> None:
        """Initialise the job."""
        self._breakers = breakers
        self._metrics = metrics

    async def run(self) -> int:
        """Export breaker state gauges.

        Returns:
            The number of breakers exported.
        """
        snapshots = self._breakers.snapshots()  # type: ignore[attr-defined]
        state_value = {"closed": 0, "half_open": 1, "open": 2}
        for name, snapshot in snapshots.items():
            self._metrics.set_gauge(
                "gateway_circuit_state",
                float(state_value.get(snapshot.state.value, 0)),
                labels={"provider": name},
            )
        return len(snapshots)


__all__ = [
    "ConversationCleanupJob",
    "DlqReplayJob",
    "OutboxRelayJob",
    "TelemetryExportJob",
    "UsageAggregationJob",
]
