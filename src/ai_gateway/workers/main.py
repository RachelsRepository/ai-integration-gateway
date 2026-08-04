"""Worker process entrypoint."""

from __future__ import annotations

import asyncio

from ai_gateway.config.settings import get_settings
from ai_gateway.container import build_container
from ai_gateway.observability.logging import configure_logging, get_logger
from ai_gateway.workers.jobs import (
    ConversationCleanupJob,
    DlqReplayJob,
    OutboxRelayJob,
    TelemetryExportJob,
    UsageAggregationJob,
)
from ai_gateway.workers.runner import WorkerRunner

logger = get_logger(__name__)


async def _amain() -> None:
    settings = get_settings()
    configure_logging(
        level=settings.observability.log_level,
        json_output=settings.observability.log_format == "json",
        service_name=f"{settings.service_name}-worker",
        version=settings.version,
        environment=settings.environment.value,
    )
    container = await build_container(settings)
    publisher = container.event_publisher
    dlq = container.dlq
    if publisher is None or dlq is None:
        raise RuntimeError("Event publisher and DLQ must be configured for workers")
    runner = WorkerRunner()

    usage = UsageAggregationJob(
        container.services.uow_factory, batch_size=settings.workers.batch_size
    )
    cleanup = ConversationCleanupJob(
        container.services.uow_factory,
        container.services.clock,
        retention_days=settings.workers.conversation_retention_days,
        batch_size=settings.workers.batch_size,
    )
    outbox = OutboxRelayJob(
        container.services.uow_factory,
        publisher,
        container.services.clock,
        dlq,
        batch_size=settings.kafka.outbox_batch_size,
        max_attempts=settings.resilience.dlq_max_attempts,
    )
    replay = DlqReplayJob(dlq, publisher, container.services.clock, container.services.metrics)
    telemetry = TelemetryExportJob(container.services.breakers, container.services.metrics)

    runner.schedule(
        "usage_aggregation",
        usage.run,
        interval_seconds=settings.workers.usage_aggregation_interval_seconds,
    )
    runner.schedule(
        "conversation_cleanup",
        cleanup.run,
        interval_seconds=settings.workers.conversation_cleanup_interval_seconds,
    )
    runner.schedule(
        "outbox_relay",
        outbox.run,
        interval_seconds=settings.kafka.outbox_poll_interval_seconds,
    )
    runner.schedule(
        "dlq_replay",
        replay.run,
        interval_seconds=settings.workers.dlq_interval_seconds,
    )
    runner.schedule(
        "telemetry_export",
        telemetry.run,
        interval_seconds=settings.workers.telemetry_export_interval_seconds,
    )

    logger.info("workers_started")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.stop()
        await container.aclose()


def main() -> None:
    """Run the worker process."""
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
