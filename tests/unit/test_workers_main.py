"""Worker process entrypoint tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_gateway.infrastructure.persistence.memory import InMemoryUnitOfWork
from ai_gateway.workers import main as workers_main


@pytest.mark.asyncio
async def test_amain_wires_jobs_and_shuts_down() -> None:
    mock_runner = MagicMock()
    mock_runner.schedule = MagicMock()
    mock_runner.stop = AsyncMock()

    mock_publisher = MagicMock()
    mock_publisher.start = AsyncMock()
    mock_publisher.stop = AsyncMock()

    mock_providers = MagicMock()
    mock_providers.aclose = AsyncMock()

    from ai_gateway.infrastructure.clock import SystemClock

    mock_services = MagicMock()
    mock_services.uow_factory = InMemoryUnitOfWork
    mock_services.clock = SystemClock()
    mock_services.metrics = MagicMock()
    mock_services.breakers = MagicMock()
    mock_services.providers = mock_providers

    mock_container = MagicMock()
    mock_container.services = mock_services

    mock_settings = MagicMock()
    mock_settings.observability.log_level = "INFO"
    mock_settings.observability.log_format = "console"
    mock_settings.service_name = "gateway"
    mock_settings.version = "1.0.0"
    mock_settings.environment.value = "local"
    mock_settings.workers.batch_size = 100
    mock_settings.workers.conversation_retention_days = 30
    mock_settings.workers.usage_aggregation_interval_seconds = 60.0
    mock_settings.workers.conversation_cleanup_interval_seconds = 60.0
    mock_settings.workers.dlq_interval_seconds = 60.0
    mock_settings.workers.telemetry_export_interval_seconds = 60.0
    mock_settings.kafka.outbox_batch_size = 100
    mock_settings.kafka.outbox_poll_interval_seconds = 60.0
    mock_settings.resilience.dlq_max_attempts = 5

    wait_event = AsyncMock()
    wait_event.wait = AsyncMock(return_value=None)

    with (
        patch.object(workers_main, "get_settings", return_value=mock_settings),
        patch.object(workers_main, "configure_logging"),
        patch.object(workers_main, "build_container", AsyncMock(return_value=mock_container)),
        patch.object(workers_main, "InMemoryEventPublisher", return_value=mock_publisher),
        patch.object(workers_main, "InMemoryDeadLetterQueue"),
        patch.object(workers_main, "WorkerRunner", return_value=mock_runner),
        patch("asyncio.Event", return_value=wait_event),
    ):
        await workers_main._amain()

    assert mock_runner.schedule.call_count == 5
    mock_runner.stop.assert_awaited_once()
    mock_publisher.stop.assert_awaited_once()
    mock_providers.aclose.assert_awaited_once()


def test_main_invokes_asyncio_run() -> None:
    with patch("asyncio.run") as mock_run:
        workers_main.main()
        mock_run.assert_called_once()
        coro = mock_run.call_args[0][0]
        coro.close()
