"""In-memory unit of work and repositories.

Provides a complete, transactionally-staged persistence backend for unit tests and local
development. Mutations are held in a staging area until ``commit`` and discarded on
``rollback``, matching the SQLAlchemy unit of work semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, date, datetime
from types import TracebackType
from typing import Any, Self

from ai_gateway.application.ports.repositories import (
    AgentRunRepository,
    ApiKeyRepository,
    AuditRepository,
    ConversationRepository,
    OutboxRepository,
    PromptRepository,
    TenantRepository,
    UsageRepository,
)
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
    new_id,
)


class _Store:
    """Shared durable state across units of work in a process."""

    def __init__(self) -> None:
        self.tenants: dict[str, Tenant] = {}
        self.api_keys: dict[str, ApiKey] = {}
        self.conversations: dict[str, Conversation] = {}
        self.prompts: dict[str, PromptTemplate] = {}
        self.agent_runs: dict[str, AgentRun] = {}
        self.usage_records: dict[str, UsageRecord] = {}
        self.usage_aggregated: set[str] = set()
        self.usage_aggregates: dict[str, UsageAggregate] = {}
        self.audit: list[AuditEvent] = []
        self.outbox: dict[str, DomainEvent] = {}
        self.outbox_published: set[str] = set()
        self.outbox_attempts: dict[str, int] = {}


class InMemoryTenantRepository:
    """In-memory tenant repository."""

    def __init__(self, store: _Store, staging: _Store) -> None:
        self._store = store
        self._staging = staging

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        """Fetch a tenant."""
        tenant = self._staging.tenants.get(tenant_id) or self._store.tenants.get(tenant_id)
        return deepcopy(tenant) if tenant else None

    async def upsert(self, tenant: Tenant) -> None:
        """Stage a tenant write."""
        self._staging.tenants[tenant.id] = deepcopy(tenant)

    async def list_active(self) -> Sequence[Tenant]:
        """List active tenants."""
        merged = {**self._store.tenants, **self._staging.tenants}
        return [deepcopy(t) for t in merged.values() if t.is_active]


class InMemoryApiKeyRepository:
    """In-memory API key repository."""

    def __init__(self, store: _Store, staging: _Store) -> None:
        self._store = store
        self._staging = staging

    async def find_by_prefix(self, prefix: str) -> Sequence[ApiKey]:
        """Fetch keys sharing a prefix."""
        merged = {**self._store.api_keys, **self._staging.api_keys}
        return [deepcopy(k) for k in merged.values() if k.prefix == prefix]

    async def add(self, api_key: ApiKey) -> None:
        """Stage a credential insert."""
        self._staging.api_keys[api_key.id] = deepcopy(api_key)

    async def revoke(self, key_id: str, *, at: datetime) -> None:
        """Stage a credential revocation."""
        key = self._staging.api_keys.get(key_id) or self._store.api_keys.get(key_id)
        if key is None:
            return
        self._staging.api_keys[key_id] = ApiKey(
            tenant_id=key.tenant_id,
            prefix=key.prefix,
            hashed_secret=key.hashed_secret,
            id=key.id,
            name=key.name,
            roles=key.roles,
            scopes=key.scopes,
            created_at=key.created_at,
            expires_at=key.expires_at,
            last_used_at=key.last_used_at,
            revoked_at=at,
        )

    async def touch(self, key_id: str, *, at: datetime) -> None:
        """Stage a last-used update."""
        key = self._staging.api_keys.get(key_id) or self._store.api_keys.get(key_id)
        if key is None:
            return
        self._staging.api_keys[key_id] = ApiKey(
            tenant_id=key.tenant_id,
            prefix=key.prefix,
            hashed_secret=key.hashed_secret,
            id=key.id,
            name=key.name,
            roles=key.roles,
            scopes=key.scopes,
            created_at=key.created_at,
            expires_at=key.expires_at,
            last_used_at=at,
            revoked_at=key.revoked_at,
        )


class InMemoryConversationRepository:
    """In-memory conversation repository with tenant isolation."""

    def __init__(self, store: _Store, staging: _Store) -> None:
        self._store = store
        self._staging = staging

    async def get(
        self, conversation_id: ConversationId, *, tenant_id: TenantId
    ) -> Conversation | None:
        """Fetch a tenant-scoped conversation."""
        conversation = self._staging.conversations.get(
            conversation_id
        ) or self._store.conversations.get(conversation_id)
        if conversation is None or conversation.tenant_id != tenant_id:
            return None
        return deepcopy(conversation)

    async def save(self, conversation: Conversation) -> None:
        """Stage a conversation write."""
        self._staging.conversations[conversation.id] = deepcopy(conversation)

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> Sequence[Conversation]:
        """List conversations for a tenant."""
        merged = {**self._store.conversations, **self._staging.conversations}
        rows = [deepcopy(c) for c in merged.values() if c.tenant_id == tenant_id]
        rows.sort(key=lambda c: c.updated_at, reverse=True)
        return rows[offset : offset + limit]

    async def delete_stale(self, *, older_than: datetime, limit: int = 500) -> int:
        """Delete idle conversations."""
        victims = [
            cid
            for cid, conversation in self._store.conversations.items()
            if conversation.updated_at < older_than
        ][:limit]
        for cid in victims:
            self._store.conversations.pop(cid, None)
            self._staging.conversations.pop(cid, None)
        return len(victims)


class InMemoryPromptRepository:
    """In-memory prompt repository."""

    def __init__(self, store: _Store, staging: _Store) -> None:
        self._store = store
        self._staging = staging

    def _key(self, tenant_id: TenantId, name: str) -> str:
        return f"{tenant_id}:{name}"

    async def get_by_name(self, tenant_id: TenantId, name: str) -> PromptTemplate | None:
        """Fetch a prompt by name."""
        key = self._key(tenant_id, name)
        prompt = self._staging.prompts.get(key) or self._store.prompts.get(key)
        return deepcopy(prompt) if prompt else None

    async def save(self, prompt: PromptTemplate) -> None:
        """Stage a prompt write."""
        self._staging.prompts[self._key(prompt.tenant_id, prompt.name)] = deepcopy(prompt)

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 100, offset: int = 0
    ) -> Sequence[PromptTemplate]:
        """List prompts for a tenant."""
        merged = {**self._store.prompts, **self._staging.prompts}
        rows = [deepcopy(p) for p in merged.values() if p.tenant_id == tenant_id]
        rows.sort(key=lambda p: p.updated_at, reverse=True)
        return rows[offset : offset + limit]


class InMemoryAgentRunRepository:
    """In-memory agent run repository."""

    def __init__(self, store: _Store, staging: _Store) -> None:
        self._store = store
        self._staging = staging

    async def save(self, run: AgentRun) -> None:
        """Stage an agent run write."""
        self._staging.agent_runs[run.id] = deepcopy(run)

    async def get(self, run_id: AgentRunId, *, tenant_id: TenantId) -> AgentRun | None:
        """Fetch a tenant-scoped agent run."""
        run = self._staging.agent_runs.get(run_id) or self._store.agent_runs.get(run_id)
        if run is None or run.tenant_id != tenant_id:
            return None
        return deepcopy(run)


class InMemoryUsageRepository:
    """In-memory usage repository."""

    def __init__(self, store: _Store, staging: _Store) -> None:
        self._store = store
        self._staging = staging

    async def record(self, usage: UsageRecord) -> None:
        """Stage a usage record append."""
        self._staging.usage_records[usage.id] = deepcopy(usage)

    async def snapshot(
        self, tenant_id: TenantId, *, at: datetime
    ) -> dict[QuotaPeriod, UsageSnapshot]:
        """Compute current daily and monthly consumption."""
        moment = at.astimezone(UTC)
        day_key = moment.date().isoformat()
        month_key = f"{moment.year:04d}-{moment.month:02d}"
        merged = {**self._store.usage_records, **self._staging.usage_records}
        daily = UsageSnapshot(period=QuotaPeriod.DAILY)
        monthly = UsageSnapshot(period=QuotaPeriod.MONTHLY)
        for record in merged.values():
            if record.tenant_id != tenant_id:
                continue
            if record.usage_date.isoformat() == day_key:
                daily = UsageSnapshot(
                    period=QuotaPeriod.DAILY,
                    requests=daily.requests + 1,
                    tokens=daily.tokens + record.usage.total_tokens,
                    cost=daily.cost + record.cost,
                )
            if record.billing_month == month_key:
                monthly = UsageSnapshot(
                    period=QuotaPeriod.MONTHLY,
                    requests=monthly.requests + 1,
                    tokens=monthly.tokens + record.usage.total_tokens,
                    cost=monthly.cost + record.cost,
                )
        return {QuotaPeriod.DAILY: daily, QuotaPeriod.MONTHLY: monthly}

    async def unaggregated(self, *, limit: int = 1000) -> Sequence[UsageRecord]:
        """Fetch records awaiting aggregation."""
        merged = {**self._store.usage_records, **self._staging.usage_records}
        pending = [
            deepcopy(r) for rid, r in merged.items() if rid not in self._store.usage_aggregated
        ]
        return pending[:limit]

    async def mark_aggregated(self, record_ids: Sequence[str]) -> None:
        """Mark records as aggregated."""
        self._store.usage_aggregated.update(record_ids)

    async def upsert_aggregate(self, aggregate: UsageAggregate) -> None:
        """Stage an aggregate upsert."""
        key = f"{aggregate.tenant_id}:{aggregate.period_key}:{aggregate.model}"
        existing = self._store.usage_aggregates.get(key) or self._staging.usage_aggregates.get(key)
        if existing is None:
            self._staging.usage_aggregates[key] = deepcopy(aggregate)
            return
        existing.requests = aggregate.requests
        existing.failed_requests = aggregate.failed_requests
        existing.cached_requests = aggregate.cached_requests
        existing.usage = aggregate.usage
        existing.cost = aggregate.cost
        existing.updated_at = aggregate.updated_at
        self._staging.usage_aggregates[key] = deepcopy(existing)

    async def aggregates_for(
        self, tenant_id: TenantId, *, since: date, until: date
    ) -> Sequence[UsageAggregate]:
        """Return roll-ups within a date range."""
        merged = {**self._store.usage_aggregates, **self._staging.usage_aggregates}
        rows = [
            deepcopy(a)
            for a in merged.values()
            if a.tenant_id == tenant_id
            and since.isoformat() <= a.period_key[:10] <= until.isoformat()
        ]
        return rows


class InMemoryAuditRepository:
    """In-memory audit repository."""

    def __init__(self, store: _Store, staging: _Store) -> None:
        self._store = store
        self._staging = staging

    async def append(self, event: AuditEvent) -> None:
        """Stage an audit append."""
        self._staging.audit.append(deepcopy(event))

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 100, offset: int = 0
    ) -> Sequence[AuditEvent]:
        """List audit events for a tenant."""
        rows = [
            deepcopy(e)
            for e in (*self._store.audit, *self._staging.audit)
            if e.tenant_id == tenant_id
        ]
        rows.sort(key=lambda e: e.occurred_at, reverse=True)
        return rows[offset : offset + limit]

    async def purge_older_than(self, cutoff: datetime, *, limit: int = 1000) -> int:
        """Delete expired audit events."""
        keep = [e for e in self._store.audit if e.occurred_at >= cutoff]
        removed = len(self._store.audit) - len(keep)
        if removed > limit:
            # Keep the newest ``limit`` victims for this pass.
            victims = sorted(
                (e for e in self._store.audit if e.occurred_at < cutoff),
                key=lambda e: e.occurred_at,
            )[:limit]
            victim_ids = {e.id for e in victims}
            self._store.audit = [e for e in self._store.audit if e.id not in victim_ids]
            return len(victims)
        self._store.audit = keep
        return removed


class InMemoryOutboxRepository:
    """In-memory transactional outbox."""

    def __init__(self, store: _Store, staging: _Store) -> None:
        self._store = store
        self._staging = staging

    async def enqueue(self, event: DomainEvent) -> None:
        """Stage an outbox insert."""
        self._staging.outbox[event.id or new_id()] = deepcopy(event)

    async def fetch_unpublished(self, *, limit: int = 100) -> Sequence[tuple[str, DomainEvent]]:
        """Claim unpublished events."""
        rows = [
            (oid, deepcopy(event))
            for oid, event in self._store.outbox.items()
            if oid not in self._store.outbox_published
        ]
        return rows[:limit]

    async def mark_published(self, outbox_ids: Sequence[str], *, at: datetime) -> None:
        """Mark events as published."""
        del at
        self._store.outbox_published.update(outbox_ids)

    async def mark_failed(self, outbox_id: str, *, error: str) -> int:
        """Record a publication failure."""
        del error
        attempts = self._store.outbox_attempts.get(outbox_id, 0) + 1
        self._store.outbox_attempts[outbox_id] = attempts
        return attempts


class InMemoryUnitOfWork:
    """In-memory unit of work with commit/rollback semantics."""

    _SHARED = _Store()
    tenants: TenantRepository
    api_keys: ApiKeyRepository
    conversations: ConversationRepository
    prompts: PromptRepository
    agent_runs: AgentRunRepository
    usage: UsageRepository
    audit: AuditRepository
    outbox: OutboxRepository

    def __init__(self, store: _Store | None = None) -> None:
        """Initialise the unit of work.

        Args:
            store: Shared durable store; the process-wide store when omitted.
        """
        self._store = store or InMemoryUnitOfWork._SHARED
        self._staging = _Store()
        self._bind_repos()
        self._active = False

    def _bind_repos(self) -> None:
        self.tenants = InMemoryTenantRepository(self._store, self._staging)
        self.api_keys = InMemoryApiKeyRepository(self._store, self._staging)
        self.conversations = InMemoryConversationRepository(self._store, self._staging)
        self.prompts = InMemoryPromptRepository(self._store, self._staging)
        self.agent_runs = InMemoryAgentRunRepository(self._store, self._staging)
        self.usage = InMemoryUsageRepository(self._store, self._staging)
        self.audit = InMemoryAuditRepository(self._store, self._staging)
        self.outbox = InMemoryOutboxRepository(self._store, self._staging)

    @classmethod
    def reset(cls) -> None:
        """Drop every durable record. Intended for test isolation."""
        cls._SHARED = _Store()

    async def __aenter__(self) -> Self:
        """Begin a transaction."""
        self._staging = _Store()
        self._bind_repos()
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Commit on success, roll back on failure."""
        if exc_type is None and self._active:
            await self.commit()
        else:
            await self.rollback()

    async def commit(self) -> None:
        """Apply staged mutations to the durable store."""
        self._store.tenants.update(self._staging.tenants)
        self._store.api_keys.update(self._staging.api_keys)
        self._store.conversations.update(self._staging.conversations)
        self._store.prompts.update(self._staging.prompts)
        self._store.agent_runs.update(self._staging.agent_runs)
        self._store.usage_records.update(self._staging.usage_records)
        self._store.usage_aggregates.update(self._staging.usage_aggregates)
        self._store.audit.extend(self._staging.audit)
        self._store.outbox.update(self._staging.outbox)
        self._staging = _Store()
        self._active = False

    async def rollback(self) -> None:
        """Discard staged mutations."""
        self._staging = _Store()
        self._active = False

    async def execute_raw(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        """No-op raw execution used by health probes in memory mode."""
        del statement, params
        return 1


__all__ = ["InMemoryUnitOfWork"]
