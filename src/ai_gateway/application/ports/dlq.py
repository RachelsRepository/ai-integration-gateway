"""Dead-letter queue port."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ai_gateway.domain.value_objects.identifiers import TenantId


@dataclass(frozen=True, slots=True)
class DeadLetterRecord:
    """A unit of work that exhausted its retry budget.

    Attributes:
        id: Stable record identifier.
        kind: Work type, for example ``event_publish`` or ``usage_record``.
        payload: Serialised work item.
        error: Last failure description.
        attempts: Number of attempts already made.
        tenant_id: Owning tenant, when the work is tenant-scoped.
        enqueued_at: Time the record entered the queue.
        next_attempt_at: Earliest time a replay should be attempted.
    """

    id: str
    kind: str
    payload: dict[str, Any]
    error: str
    attempts: int = 0
    tenant_id: TenantId | None = None
    enqueued_at: datetime | None = None
    next_attempt_at: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class DeadLetterQueue(Protocol):
    """Stores work that could not be completed and supports controlled replay."""

    async def put(self, record: DeadLetterRecord) -> None:
        """Add a record to the queue.

        Args:
            record: The failed work item.
        """
        ...

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
        ...

    async def resolve(self, record_id: str) -> None:
        """Remove a record after a successful replay.

        Args:
            record_id: Record identifier.
        """
        ...

    async def reschedule(self, record_id: str, *, next_attempt_at: datetime, error: str) -> None:
        """Return a record to the queue with a later attempt time.

        Args:
            record_id: Record identifier.
            next_attempt_at: Earliest time for the next replay.
            error: Failure description from the latest attempt.
        """
        ...

    async def size(self) -> int:
        """Return the number of records currently queued."""
        ...


__all__ = ["DeadLetterQueue", "DeadLetterRecord"]
