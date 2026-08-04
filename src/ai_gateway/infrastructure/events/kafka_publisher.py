"""Kafka event publisher.

When Kafka is disabled or unreachable the publisher fails soft: events remain in the
outbox and the background relay retries later. Direct ``publish`` calls raise so that
callers can decide whether to fall back to the outbox.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from ai_gateway.domain.errors import DomainError
from ai_gateway.domain.events import DomainEvent
from ai_gateway.observability.logging import get_logger

logger = get_logger(__name__)


class KafkaEventPublisher:
    """Publishes domain events to Kafka topics."""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        client_id: str = "ai-gateway",
        topic_prefix: str = "",
        enabled: bool = True,
        **producer_kwargs: Any,
    ) -> None:
        """Initialise the publisher.

        Args:
            bootstrap_servers: Kafka bootstrap servers.
            client_id: Kafka client identifier.
            topic_prefix: Optional prefix prepended to every topic.
            enabled: Whether the publisher is active.
            **producer_kwargs: Additional aiokafka producer options.
        """
        self._bootstrap = bootstrap_servers
        self._client_id = client_id
        self._prefix = topic_prefix
        self._enabled = enabled
        self._producer_kwargs = producer_kwargs
        self._producer: Any = None

    async def start(self) -> None:
        """Start the underlying producer with bounded retries."""
        if not self._enabled:
            return
        from aiokafka import AIOKafkaProducer

        last_error: Exception | None = None
        for attempt in range(1, 9):
            producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                client_id=self._client_id,
                acks="all",
                **self._producer_kwargs,
            )
            self._producer = producer
            try:
                await producer.start()
                logger.info(
                    "kafka_publisher_started",
                    bootstrap=self._bootstrap,
                    attempt=attempt,
                )
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "kafka_publisher_start_retry",
                    bootstrap=self._bootstrap,
                    attempt=attempt,
                    error=str(exc),
                )
                try:
                    await producer.stop()
                except Exception as stop_exc:
                    logger.debug("kafka_publisher_stop_failed", error=str(stop_exc))
                self._producer = None
                await asyncio.sleep(min(2 ** (attempt - 1), 10))
        raise DomainError(f"Kafka publisher failed to start: {last_error}")

    async def stop(self) -> None:
        """Flush and stop the underlying producer."""
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, event: DomainEvent) -> None:
        """Publish a single event.

        Args:
            event: Event to publish.

        Raises:
            DomainError: If the publisher is disabled or not started.
        """
        await self.publish_batch([event])

    async def publish_batch(self, events: Sequence[DomainEvent]) -> None:
        """Publish a batch of events.

        Args:
            events: Events to publish.

        Raises:
            DomainError: If the publisher is disabled or not started.
        """
        if not self._enabled:
            raise DomainError("Kafka publisher is disabled")
        if self._producer is None:
            raise DomainError("Kafka publisher has not been started")
        for event in events:
            topic = f"{self._prefix}{event.topic}" if self._prefix else event.topic
            payload = json.dumps(event.to_dict()).encode("utf-8")
            await self._producer.send_and_wait(
                topic, payload, key=event.partition_key.encode("utf-8")
            )


__all__ = ["KafkaEventPublisher"]
