"""Audit trail entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId, UserId, new_id


class AuditOutcome(StrEnum):
    """Result of an audited action."""

    ALLOWED = "allowed"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable record of a security-relevant action.

    Audit events never contain prompt or completion text. They record who did what, to
    which resource, from where, and with what outcome.

    Attributes:
        tenant_id: Tenant in whose context the action occurred.
        action: Dotted action name, for example ``chat.completion``.
        outcome: Result of the action.
        id: Stable event identifier.
        request_id: Correlating request identifier.
        actor: Subject identifier of the acting principal.
        user_id: Optional end-user attribution.
        resource: Identifier of the affected resource.
        source_ip: Client address, when available.
        user_agent: Client user agent, when available.
        occurred_at: Event timestamp in UTC.
        attributes: Additional non-sensitive structured context.
    """

    tenant_id: TenantId
    action: str
    outcome: AuditOutcome
    id: str = field(default_factory=new_id)
    request_id: RequestId | None = None
    actor: str | None = None
    user_id: UserId | None = None
    resource: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the event for structured log or event-bus emission.

        Returns:
            A JSON-serialisable mapping.
        """
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "action": self.action,
            "outcome": self.outcome.value,
            "request_id": self.request_id,
            "actor": self.actor,
            "user_id": self.user_id,
            "resource": self.resource,
            "source_ip": self.source_ip,
            "user_agent": self.user_agent,
            "occurred_at": self.occurred_at.isoformat(),
            "attributes": self.attributes,
        }


__all__ = ["AuditEvent", "AuditOutcome"]
