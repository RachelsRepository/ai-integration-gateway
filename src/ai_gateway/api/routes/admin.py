"""Local/admin operational endpoints (DLQ re-drive, quota/circuit test helpers)."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import Field

from ai_gateway.api.deps import get_container, get_request_context
from ai_gateway.api.schemas import APIModel
from ai_gateway.application.dto import RequestContext
from ai_gateway.application.ports.dlq import DeadLetterRecord
from ai_gateway.container import AppContainer
from ai_gateway.domain.entities.tenant import Permission, Quota, QuotaPeriod, Tenant
from ai_gateway.domain.errors import AuthorizationError, NotFoundError, ValidationError
from ai_gateway.domain.events import DomainEvent, EventType
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.money import Money

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class DemoQuotaUpdate(APIModel):
    """Replace demo-tenant quotas for local multi-replica race proofs."""

    max_requests: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_cost_micros: int | None = Field(default=None, ge=1)
    period: str = "daily"
    rate_limit_burst: int | None = Field(default=None, ge=1)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)


class DlqRedriveResponse(APIModel):
    """Result of an authenticated DLQ re-drive."""

    record_id: str
    status: str
    attempts: int = 0


def _require_local(container: AppContainer) -> None:
    if not container.settings.is_local:
        raise AuthorizationError("Admin operational endpoints are local/test only")


@router.put("/tenants/{tenant_id}/quotas")
async def update_tenant_quotas(
    tenant_id: str,
    body: DemoQuotaUpdate,
    context: Annotated[RequestContext, Depends(get_request_context)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> dict[str, Any]:
    """Update tenant quotas (local verification only)."""
    _require_local(container)
    context.principal.require(Permission.TENANT_ADMIN)
    if str(context.tenant_id) != tenant_id and tenant_id != "me":
        raise AuthorizationError("Cannot mutate another tenant's quotas")
    target = context.tenant_id if tenant_id == "me" else TenantId(tenant_id)
    period = QuotaPeriod(body.period)
    quota = Quota(
        period=period,
        max_requests=body.max_requests,
        max_tokens=body.max_tokens,
        max_cost=Money.from_micros(body.max_cost_micros) if body.max_cost_micros else None,
    )
    async with container.services.uow_factory() as uow:
        tenant = await uow.tenants.get(target)
        if tenant is None:
            raise NotFoundError("Tenant not found", details={"tenant_id": tenant_id})
        updated = Tenant(
            name=tenant.name,
            id=tenant.id,
            status=tenant.status,
            quotas={**tenant.quotas, period: quota},
            rate_limit_per_minute=body.rate_limit_per_minute or tenant.rate_limit_per_minute,
            rate_limit_burst=body.rate_limit_burst or tenant.rate_limit_burst,
            routing=tenant.routing,
            pii_redaction_enabled=tenant.pii_redaction_enabled,
            injection_detection_enabled=tenant.injection_detection_enabled,
            audit_retention_days=tenant.audit_retention_days,
            created_at=tenant.created_at,
            metadata=dict(tenant.metadata),
        )
        await uow.tenants.upsert(updated)
        await uow.commit()
    # Best-effort: clear shared reservation counters so the new ceiling is observable.
    client = container._redis_client
    if client is not None:
        day = datetime.now(UTC).strftime("%Y%m%d")
        month = datetime.now(UTC).strftime("%Y%m")
        minute = datetime.now(UTC).strftime("%Y%m%d%H%M")
        for key in (
            f"aigw:quota:{target}:day:{day}:tokens",
            f"aigw:quota:{target}:day:{day}:cost",
            f"aigw:quota:{target}:month:{month}:tokens",
            f"aigw:quota:{target}:month:{month}:cost",
            f"aigw:quota:{target}:rpm:{minute}",
            f"aigw:quota:{target}:inflight",
        ):
            with suppress(Exception):
                await client.delete(key)
    return {"tenant_id": str(target), "quotas": {period.value: body.model_dump()}}


@router.post("/circuits/reset")
async def reset_circuits(
    context: Annotated[RequestContext, Depends(get_request_context)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> dict[str, str]:
    """Clear Redis-backed circuit breaker keys (local verification only)."""
    _require_local(container)
    context.principal.require(Permission.TENANT_ADMIN)
    client = container._redis_client
    if client is None:
        return {"status": "skipped", "reason": "no_redis"}
    deleted = 0
    async for key in client.scan_iter(match="aigw:cb:*"):
        await client.delete(key)
        deleted += 1
    return {"status": "ok", "deleted": str(deleted)}


@router.get("/dlq")
async def list_dlq(
    context: Annotated[RequestContext, Depends(get_request_context)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> dict[str, Any]:
    """List durable DLQ depth for manual review."""
    _require_local(container)
    context.principal.require(Permission.TENANT_ADMIN)
    dlq = container.dlq
    if dlq is None:
        raise ValidationError("DLQ is not configured")
    size = await dlq.size()
    claimed = await dlq.claim(limit=50)
    return {
        "size": size,
        "records": [
            {
                "id": r.id,
                "kind": r.kind,
                "error": r.error,
                "attempts": r.attempts,
                "tenant_id": str(r.tenant_id) if r.tenant_id else None,
                "enqueued_at": r.enqueued_at.isoformat() if r.enqueued_at else None,
            }
            for r in claimed
        ],
    }


@router.post("/dlq/{record_id}/redrive", response_model=DlqRedriveResponse)
async def redrive_dlq(
    record_id: str,
    context: Annotated[RequestContext, Depends(get_request_context)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> DlqRedriveResponse:
    """Idempotent authenticated re-drive of a durable DLQ record.

    Re-drive publishes via the event publisher when payload matches outbox shape.
    Secrets and raw prompts must not be stored in DLQ payloads by producers.
    """
    _require_local(container)
    context.principal.require(Permission.TENANT_ADMIN)
    dlq = container.dlq
    publisher = container.event_publisher
    if dlq is None:
        raise ValidationError("DLQ is not configured")
    records = await dlq.claim(limit=200)
    match = next((r for r in records if r.id == record_id), None)
    if match is None:
        # Idempotent: already resolved counts as success.
        return DlqRedriveResponse(record_id=record_id, status="already_resolved", attempts=0)
    if match.tenant_id and match.tenant_id != context.tenant_id:
        raise AuthorizationError("DLQ record belongs to another tenant")
    try:
        if publisher is not None and match.kind == "event_publish":
            payload = match.payload
            event_type = EventType(str(payload.get("type", EventType.AUDIT_LOGGED.value)))
            event = DomainEvent(
                type=event_type,
                tenant_id=match.tenant_id or context.tenant_id,
                payload=dict(payload.get("payload") or {"redrive": True}),
                id=str(payload.get("id") or record_id),
                request_id=(
                    RequestId(str(payload["request_id"])) if payload.get("request_id") else None
                ),
            )
            await publisher.publish(event)
        await dlq.resolve(record_id)
        container.services.metrics.increment("gateway_dlq_redrive_total", labels={"result": "ok"})
        return DlqRedriveResponse(record_id=record_id, status="resolved", attempts=match.attempts)
    except Exception as exc:
        await dlq.reschedule(
            record_id,
            next_attempt_at=datetime.now(UTC),
            error=f"redrive_failed:{type(exc).__name__}",
        )
        container.services.metrics.increment(
            "gateway_dlq_redrive_total", labels={"result": "error"}
        )
        raise ValidationError(
            "DLQ re-drive failed",
            details={"record_id": record_id, "error": type(exc).__name__},
        ) from exc


@router.post("/dlq/seed")
async def seed_dlq(
    context: Annotated[RequestContext, Depends(get_request_context)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> dict[str, str]:
    """Insert a non-secret synthetic DLQ row for compose verification."""
    _require_local(container)
    context.principal.require(Permission.TENANT_ADMIN)
    dlq = container.dlq
    if dlq is None:
        raise ValidationError("DLQ is not configured")
    record_id = str(context.request_id)[:36]
    await dlq.put(
        DeadLetterRecord(
            id=record_id,
            kind="event_publish",
            payload={
                "type": EventType.AUDIT_LOGGED.value,
                "tenant_id": str(context.tenant_id),
                "request_id": str(context.request_id),
                "payload": {"source": "admin_seed", "request_ref": str(context.request_id)},
            },
            error="synthetic_poison_for_verification",
            attempts=0,
            tenant_id=context.tenant_id,
            enqueued_at=datetime.now(UTC),
            next_attempt_at=datetime.now(UTC),
            metadata={"source": "admin_seed"},
        )
    )
    return {"record_id": record_id, "status": "seeded"}


__all__ = ["router"]
