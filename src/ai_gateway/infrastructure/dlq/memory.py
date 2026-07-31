"""In-memory dead-letter queue."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime

from ai_gateway.application.ports.dlq import DeadLetterRecord


class InMemoryDeadLetterQueue:
    """Process-local dead-letter queue."""

    def __init__(self) -> None:
        """Initialise an empty queue."""
        self._records: dict[str, DeadLetterRecord] = {}

    async def put(self, record: DeadLetterRecord) -> None:
        """Add a record.

        Args:
            record: Failed work item.
        """
        stored = DeadLetterRecord(
            id=record.id,
            kind=record.kind,
            payload=deepcopy(record.payload),
            error=record.error,
            attempts=record.attempts,
            tenant_id=record.tenant_id,
            enqueued_at=record.enqueued_at or datetime.now(UTC),
            next_attempt_at=record.next_attempt_at or datetime.now(UTC),
            metadata=dict(record.metadata),
        )
        self._records[stored.id] = stored

    async def claim(
        self, *, limit: int = 50, now: datetime | None = None
    ) -> Sequence[DeadLetterRecord]:
        """Claim records that are due for replay.

        Args:
            limit: Maximum records to claim.
            now: Evaluation time.

        Returns:
            The claimed records.
        """
        moment = now or datetime.now(UTC)
        due = [
            record
            for record in self._records.values()
            if record.next_attempt_at is None or record.next_attempt_at <= moment
        ]
        due.sort(key=lambda r: r.enqueued_at or moment)
        return due[:limit]

    async def resolve(self, record_id: str) -> None:
        """Remove a successfully replayed record.

        Args:
            record_id: Record identifier.
        """
        self._records.pop(record_id, None)

    async def reschedule(self, record_id: str, *, next_attempt_at: datetime, error: str) -> None:
        """Return a record to the queue with a later attempt time.

        Args:
            record_id: Record identifier.
            next_attempt_at: Earliest time for the next replay.
            error: Failure description.
        """
        current = self._records.get(record_id)
        if current is None:
            return
        self._records[record_id] = DeadLetterRecord(
            id=current.id,
            kind=current.kind,
            payload=current.payload,
            error=error,
            attempts=current.attempts + 1,
            tenant_id=current.tenant_id,
            enqueued_at=current.enqueued_at,
            next_attempt_at=next_attempt_at,
            metadata=current.metadata,
        )

    async def size(self) -> int:
        """Return the queue depth."""
        return len(self._records)


__all__ = ["InMemoryDeadLetterQueue"]
