"""PostgreSQL-backed dead-letter queue."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_gateway.application.ports.dlq import DeadLetterRecord
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.infrastructure.persistence.models import DeadLetterModel


def _to_model(record: DeadLetterRecord) -> DeadLetterModel:
    return DeadLetterModel(
        id=record.id,
        kind=record.kind,
        payload=dict(record.payload),
        error=record.error,
        attempts=record.attempts,
        tenant_id=str(record.tenant_id) if record.tenant_id else None,
        metadata_json=dict(record.metadata),
        enqueued_at=record.enqueued_at or datetime.now(UTC),
        next_attempt_at=record.next_attempt_at or datetime.now(UTC),
    )


def _from_model(model: DeadLetterModel) -> DeadLetterRecord:
    return DeadLetterRecord(
        id=model.id,
        kind=model.kind,
        payload=dict(model.payload or {}),
        error=model.error,
        attempts=model.attempts,
        tenant_id=TenantId(model.tenant_id) if model.tenant_id else None,
        enqueued_at=model.enqueued_at,
        next_attempt_at=model.next_attempt_at,
        metadata={str(k): str(v) for k, v in (model.metadata_json or {}).items()},
    )


class SqlDeadLetterQueue:
    """Durable DLQ persisted in PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialise the durable DLQ.

        Args:
            session_factory: Async SQLAlchemy session factory.
        """
        self._session_factory = session_factory

    async def put(self, record: DeadLetterRecord) -> None:
        """Insert or replace a DLQ record."""
        async with self._session_factory() as session:
            existing = await session.get(DeadLetterModel, record.id)
            if existing is None:
                session.add(_to_model(record))
            else:
                existing.kind = record.kind
                existing.payload = dict(record.payload)
                existing.error = record.error
                existing.attempts = record.attempts
                existing.tenant_id = str(record.tenant_id) if record.tenant_id else None
                existing.metadata_json = dict(record.metadata)
                existing.next_attempt_at = record.next_attempt_at or datetime.now(UTC)
            await session.commit()

    async def claim(
        self, *, limit: int = 50, now: datetime | None = None
    ) -> Sequence[DeadLetterRecord]:
        """Claim records that are due for replay."""
        moment = now or datetime.now(UTC)
        async with self._session_factory() as session:
            stmt = (
                select(DeadLetterModel)
                .where(DeadLetterModel.next_attempt_at <= moment)
                .order_by(DeadLetterModel.enqueued_at.asc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_from_model(row) for row in rows]

    async def resolve(self, record_id: str) -> None:
        """Delete a successfully replayed record."""
        async with self._session_factory() as session:
            await session.execute(delete(DeadLetterModel).where(DeadLetterModel.id == record_id))
            await session.commit()

    async def reschedule(self, record_id: str, *, next_attempt_at: datetime, error: str) -> None:
        """Bump attempts and schedule a later replay."""
        async with self._session_factory() as session:
            await session.execute(
                update(DeadLetterModel)
                .where(DeadLetterModel.id == record_id)
                .values(
                    next_attempt_at=next_attempt_at,
                    error=error,
                    attempts=DeadLetterModel.attempts + 1,
                )
            )
            await session.commit()

    async def size(self) -> int:
        """Return the number of queued records."""
        async with self._session_factory() as session:
            result = await session.execute(select(func.count()).select_from(DeadLetterModel))
            return int(result.scalar_one())


__all__ = ["SqlDeadLetterQueue"]
