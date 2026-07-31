"""Audit trail service."""

from __future__ import annotations

from typing import Any

from ai_gateway.application.dto import RequestContext
from ai_gateway.application.ports.clock import Clock
from ai_gateway.application.ports.repositories import UnitOfWork
from ai_gateway.domain.entities.audit import AuditEvent, AuditOutcome
from ai_gateway.domain.events import DomainEvent, EventType


class AuditTrail:
    """Records security-relevant actions.

    Audit rows are written inside the caller's transaction and mirrored onto the event bus
    through the outbox, so the trail survives a broker outage.
    """

    def __init__(self, *, clock: Clock) -> None:
        """Initialise the trail.

        Args:
            clock: Injected clock.
        """
        self._clock = clock

    async def record(
        self,
        uow: UnitOfWork,
        context: RequestContext,
        *,
        action: str,
        outcome: AuditOutcome = AuditOutcome.ALLOWED,
        resource: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append an audit event and mirror it to the event bus.

        Args:
            uow: Open unit of work.
            context: Request context supplying actor and correlation identifiers.
            action: Dotted action name.
            outcome: Result of the action.
            resource: Identifier of the affected resource.
            attributes: Additional non-sensitive context.

        Returns:
            The recorded event.
        """
        event = AuditEvent(
            tenant_id=context.tenant_id,
            action=action,
            outcome=outcome,
            request_id=context.request_id,
            actor=context.principal.subject,
            user_id=context.principal.user_id,
            resource=resource,
            source_ip=context.source_ip,
            user_agent=context.user_agent,
            occurred_at=self._clock.now(),
            attributes=attributes or {},
        )
        await uow.audit.append(event)
        await uow.outbox.enqueue(
            DomainEvent(
                type=EventType.AUDIT_LOGGED,
                tenant_id=context.tenant_id,
                request_id=context.request_id,
                trace_id=context.trace_id,
                payload=event.to_dict(),
            )
        )
        return event


__all__ = ["AuditTrail"]
