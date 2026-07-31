"""Persistence ports.

Repositories express intent in domain terms. Query shapes that exist purely for reporting
are separated from aggregate persistence so that the write model stays small.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

from ai_gateway.domain.entities.agent import AgentRun
from ai_gateway.domain.entities.audit import AuditEvent
from ai_gateway.domain.entities.conversation import Conversation
from ai_gateway.domain.entities.prompt import PromptTemplate
from ai_gateway.domain.entities.tenant import ApiKey, QuotaPeriod, Tenant
from ai_gateway.domain.entities.usage import UsageAggregate, UsageRecord
from ai_gateway.domain.events import DomainEvent
from ai_gateway.domain.policies.quota import UsageSnapshot
from ai_gateway.domain.value_objects.identifiers import (
    AgentRunId,
    ConversationId,
    TenantId,
)


@runtime_checkable
class TenantRepository(Protocol):
    """Reads and writes tenant configuration."""

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        """Fetch a tenant by identifier.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            The tenant, or ``None`` when unknown.
        """
        ...

    async def upsert(self, tenant: Tenant) -> None:
        """Insert or update a tenant.

        Args:
            tenant: Tenant to persist.
        """
        ...

    async def list_active(self) -> Sequence[Tenant]:
        """Return every active tenant."""
        ...


@runtime_checkable
class ApiKeyRepository(Protocol):
    """Resolves and maintains API credentials."""

    async def find_by_prefix(self, prefix: str) -> Sequence[ApiKey]:
        """Fetch candidate keys sharing a non-secret prefix.

        Args:
            prefix: Non-secret leading fragment.

        Returns:
            Candidate credentials to verify against.
        """
        ...

    async def add(self, api_key: ApiKey) -> None:
        """Persist a new credential.

        Args:
            api_key: Credential to store.
        """
        ...

    async def revoke(self, key_id: str, *, at: datetime) -> None:
        """Revoke a credential.

        Args:
            key_id: Credential identifier.
            at: Revocation timestamp.
        """
        ...

    async def touch(self, key_id: str, *, at: datetime) -> None:
        """Record successful use of a credential.

        Args:
            key_id: Credential identifier.
            at: Time of use.
        """
        ...


@runtime_checkable
class ConversationRepository(Protocol):
    """Persists conversation aggregates."""

    async def get(
        self, conversation_id: ConversationId, *, tenant_id: TenantId
    ) -> Conversation | None:
        """Fetch a conversation scoped to a tenant.

        Args:
            conversation_id: Conversation identifier.
            tenant_id: Owning tenant, enforced at the query level.

        Returns:
            The conversation, or ``None`` when unknown.
        """
        ...

    async def save(self, conversation: Conversation) -> None:
        """Insert or update a conversation and its messages.

        Args:
            conversation: Aggregate to persist.
        """
        ...

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> Sequence[Conversation]:
        """List conversations for a tenant, newest first.

        Args:
            tenant_id: Owning tenant.
            limit: Maximum rows to return.
            offset: Rows to skip.

        Returns:
            The matching conversations.
        """
        ...

    async def delete_stale(self, *, older_than: datetime, limit: int = 500) -> int:
        """Delete conversations idle since a cutoff.

        Args:
            older_than: Cutoff timestamp.
            limit: Maximum rows to delete in one pass.

        Returns:
            The number of conversations deleted.
        """
        ...


@runtime_checkable
class PromptRepository(Protocol):
    """Persists prompt templates and their immutable versions."""

    async def get_by_name(self, tenant_id: TenantId, name: str) -> PromptTemplate | None:
        """Fetch a prompt by tenant-scoped name.

        Args:
            tenant_id: Owning tenant.
            name: Prompt name.

        Returns:
            The prompt, or ``None`` when unknown.
        """
        ...

    async def save(self, prompt: PromptTemplate) -> None:
        """Insert or update a prompt and append any new versions.

        Args:
            prompt: Aggregate to persist.
        """
        ...

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 100, offset: int = 0
    ) -> Sequence[PromptTemplate]:
        """List prompts owned by a tenant.

        Args:
            tenant_id: Owning tenant.
            limit: Maximum rows to return.
            offset: Rows to skip.

        Returns:
            The matching prompts.
        """
        ...


@runtime_checkable
class AgentRunRepository(Protocol):
    """Persists agent runs and their steps."""

    async def save(self, run: AgentRun) -> None:
        """Insert or update an agent run.

        Args:
            run: Aggregate to persist.
        """
        ...

    async def get(self, run_id: AgentRunId, *, tenant_id: TenantId) -> AgentRun | None:
        """Fetch an agent run scoped to a tenant.

        Args:
            run_id: Run identifier.
            tenant_id: Owning tenant.

        Returns:
            The run, or ``None`` when unknown.
        """
        ...


@runtime_checkable
class UsageRepository(Protocol):
    """Append-only metering storage plus aggregate roll-ups."""

    async def record(self, usage: UsageRecord) -> None:
        """Append a usage record.

        Args:
            usage: Immutable metering fact.
        """
        ...

    async def snapshot(
        self, tenant_id: TenantId, *, at: datetime
    ) -> dict[QuotaPeriod, UsageSnapshot]:
        """Return current daily and monthly consumption for a tenant.

        Args:
            tenant_id: Tenant to measure.
            at: Point in time defining the periods.

        Returns:
            Snapshots keyed by enforcement period.
        """
        ...

    async def unaggregated(self, *, limit: int = 1000) -> Sequence[UsageRecord]:
        """Fetch usage records that have not yet been rolled up.

        Args:
            limit: Maximum rows to return.

        Returns:
            The pending records.
        """
        ...

    async def mark_aggregated(self, record_ids: Sequence[str]) -> None:
        """Mark usage records as rolled up.

        Args:
            record_ids: Identifiers of records that were aggregated.
        """
        ...

    async def upsert_aggregate(self, aggregate: UsageAggregate) -> None:
        """Insert or update a roll-up row.

        Args:
            aggregate: The aggregate to persist.
        """
        ...

    async def aggregates_for(
        self, tenant_id: TenantId, *, since: date, until: date
    ) -> Sequence[UsageAggregate]:
        """Return roll-ups for a tenant within a date range.

        Args:
            tenant_id: Tenant to report on.
            since: Inclusive start date.
            until: Inclusive end date.

        Returns:
            The matching aggregates.
        """
        ...


@runtime_checkable
class AuditRepository(Protocol):
    """Append-only audit trail storage."""

    async def append(self, event: AuditEvent) -> None:
        """Persist an audit event.

        Args:
            event: Immutable audit fact.
        """
        ...

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 100, offset: int = 0
    ) -> Sequence[AuditEvent]:
        """List audit events for a tenant, newest first.

        Args:
            tenant_id: Tenant to report on.
            limit: Maximum rows to return.
            offset: Rows to skip.

        Returns:
            The matching events.
        """
        ...

    async def purge_older_than(self, cutoff: datetime, *, limit: int = 1000) -> int:
        """Delete audit events beyond the retention window.

        Args:
            cutoff: Retention cutoff.
            limit: Maximum rows to delete in one pass.

        Returns:
            The number of events deleted.
        """
        ...


@runtime_checkable
class OutboxRepository(Protocol):
    """Transactional outbox used to publish events exactly once."""

    async def enqueue(self, event: DomainEvent) -> None:
        """Stage an event for publication in the current transaction.

        Args:
            event: Event to publish once the transaction commits.
        """
        ...

    async def fetch_unpublished(self, *, limit: int = 100) -> Sequence[tuple[str, DomainEvent]]:
        """Claim a batch of pending events.

        Args:
            limit: Maximum events to claim.

        Returns:
            Tuples of outbox row identifier and event.
        """
        ...

    async def mark_published(self, outbox_ids: Sequence[str], *, at: datetime) -> None:
        """Mark events as successfully published.

        Args:
            outbox_ids: Rows to mark.
            at: Publication timestamp.
        """
        ...

    async def mark_failed(self, outbox_id: str, *, error: str) -> int:
        """Record a publication failure and increment the attempt counter.

        Args:
            outbox_id: Row to mark.
            error: Failure description.

        Returns:
            The new attempt count.
        """
        ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Transactional boundary exposing every repository.

    Use cases open exactly one unit of work per request so that aggregate writes and the
    outbox insert commit atomically.
    """

    tenants: TenantRepository
    api_keys: ApiKeyRepository
    conversations: ConversationRepository
    prompts: PromptRepository
    agent_runs: AgentRunRepository
    usage: UsageRepository
    audit: AuditRepository
    outbox: OutboxRepository

    async def __aenter__(self) -> Self:
        """Begin a transaction."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Commit on success, roll back on failure."""
        ...

    async def commit(self) -> None:
        """Flush and commit the transaction."""
        ...

    async def rollback(self) -> None:
        """Abandon the transaction."""
        ...

    async def execute_raw(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a raw statement, used only by health probes and maintenance jobs.

        Args:
            statement: SQL text.
            params: Bound parameters.

        Returns:
            The driver-specific result.
        """
        ...


__all__ = [
    "AgentRunRepository",
    "ApiKeyRepository",
    "AuditRepository",
    "ConversationRepository",
    "OutboxRepository",
    "PromptRepository",
    "TenantRepository",
    "UnitOfWork",
    "UsageRepository",
]
