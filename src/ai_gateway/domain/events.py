"""Domain events published to the platform event bus.

Events are the integration contract between the gateway and downstream consumers such as
billing, analytics and security monitoring. They are versioned, additive-only, and never
carry prompt or completion text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any

from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId, new_id

EVENT_SCHEMA_VERSION = 1


class EventType(StrEnum):
    """Canonical event names published by the gateway."""

    PROMPT_SUBMITTED = "gateway.prompt.submitted"
    PROVIDER_SELECTED = "gateway.provider.selected"
    COMPLETION_RECEIVED = "gateway.completion.received"
    TOOL_EXECUTED = "gateway.tool.executed"
    CONVERSATION_UPDATED = "gateway.conversation.updated"
    PROVIDER_FAILED = "gateway.provider.failed"
    USAGE_RECORDED = "gateway.usage.recorded"
    AGENT_RUN_COMPLETED = "gateway.agent.run_completed"
    QUOTA_EXCEEDED = "gateway.quota.exceeded"
    AUDIT_LOGGED = "gateway.audit.logged"

    @property
    def topic(self) -> str:
        """Return the event-bus topic the event is published to."""
        return _TOPIC_BY_TYPE[self]


_TOPIC_BY_TYPE: dict[EventType, str] = {
    EventType.PROMPT_SUBMITTED: "gateway.requests",
    EventType.PROVIDER_SELECTED: "gateway.routing",
    EventType.COMPLETION_RECEIVED: "gateway.requests",
    EventType.TOOL_EXECUTED: "gateway.agents",
    EventType.CONVERSATION_UPDATED: "gateway.conversations",
    EventType.PROVIDER_FAILED: "gateway.routing",
    EventType.USAGE_RECORDED: "gateway.usage",
    EventType.AGENT_RUN_COMPLETED: "gateway.agents",
    EventType.QUOTA_EXCEEDED: "gateway.governance",
    EventType.AUDIT_LOGGED: "gateway.audit",
}


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """An immutable fact that has already happened.

    Attributes:
        type: Canonical event name.
        tenant_id: Tenant the event belongs to; also the partition key.
        payload: Event-specific, JSON-serialisable body.
        id: Stable event identifier used for consumer-side deduplication.
        request_id: Correlating request identifier.
        occurred_at: Event timestamp in UTC.
        schema_version: Version of the event envelope.
        trace_id: Distributed trace identifier, when sampling is active.
    """

    type: EventType
    tenant_id: TenantId
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    request_id: RequestId | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = EVENT_SCHEMA_VERSION
    trace_id: str | None = None

    @property
    def topic(self) -> str:
        """Return the destination topic for this event."""
        return self.type.topic

    @property
    def partition_key(self) -> str:
        """Return the key that guarantees per-tenant ordering."""
        return str(self.tenant_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the event envelope.

        Returns:
            A JSON-serialisable mapping.
        """
        return {
            "id": self.id,
            "type": self.type.value,
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": jsonable(self.payload),
        }


def jsonable(value: Any) -> Any:  # noqa: PLR0911 - a dispatch table would be less readable
    """Convert domain values into JSON-serialisable primitives.

    Args:
        value: Any domain value, container or primitive.

    Returns:
        A structure containing only primitives, lists and dictionaries.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {k: jsonable(v) for k, v in asdict(value).items()}
    return value


__all__ = ["EVENT_SCHEMA_VERSION", "DomainEvent", "EventType", "jsonable"]
