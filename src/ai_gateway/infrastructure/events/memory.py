"""In-memory event publisher for tests and local development."""

from __future__ import annotations

from collections.abc import Sequence

from ai_gateway.domain.events import DomainEvent


class InMemoryEventPublisher:
    """Collects published events in process memory."""

    def __init__(self) -> None:
        """Initialise an empty publisher."""
        self.events: list[DomainEvent] = []
        self.started = False

    async def publish(self, event: DomainEvent) -> None:
        """Publish a single event.

        Args:
            event: Event to record.
        """
        self.events.append(event)

    async def publish_batch(self, events: Sequence[DomainEvent]) -> None:
        """Publish a batch of events.

        Args:
            events: Events to record.
        """
        self.events.extend(events)

    async def start(self) -> None:
        """Mark the publisher as started."""
        self.started = True

    async def stop(self) -> None:
        """Mark the publisher as stopped."""
        self.started = False

    def clear(self) -> None:
        """Drop every recorded event."""
        self.events.clear()


__all__ = ["InMemoryEventPublisher"]
