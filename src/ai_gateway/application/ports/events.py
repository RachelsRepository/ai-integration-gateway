"""Event publication port."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ai_gateway.domain.events import DomainEvent


@runtime_checkable
class EventPublisher(Protocol):
    """Publishes domain events to the platform event bus."""

    async def publish(self, event: DomainEvent) -> None:
        """Publish a single event.

        Args:
            event: The event to publish.
        """
        ...

    async def publish_batch(self, events: Sequence[DomainEvent]) -> None:
        """Publish a batch of events.

        Args:
            events: Events to publish, in order.
        """
        ...

    async def start(self) -> None:
        """Start the underlying producer."""
        ...

    async def stop(self) -> None:
        """Flush and stop the underlying producer."""
        ...


__all__ = ["EventPublisher"]
