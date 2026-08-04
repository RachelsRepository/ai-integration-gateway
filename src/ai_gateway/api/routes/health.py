"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ai_gateway.api.deps import get_container
from ai_gateway.api.schemas import HealthResponse
from ai_gateway.config.settings import PersistenceBackend
from ai_gateway.container import AppContainer
from ai_gateway.observability.metrics import render_metrics

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def liveness(container: Annotated[AppContainer, Depends(get_container)]) -> HealthResponse:
    """Report process liveness.

    Returns:
        A successful health response when the process is running.
    """
    return HealthResponse(
        status="ok",
        service=container.settings.service_name,
        version=container.settings.version,
    )


@router.get("/health/ready", response_model=HealthResponse)
async def readiness(
    container: Annotated[AppContainer, Depends(get_container)], response: Response
) -> HealthResponse:
    """Report dependency readiness.

    Returns:
        Readiness status. Sets HTTP 503 when a critical dependency is down.
    """
    checks: list[dict[str, object]] = []
    healthy = True

    cache_ok = await container.services.response_cache.ping()
    checks.append({"name": "cache", "healthy": cache_ok, "critical": True})
    healthy = healthy and cache_ok

    if container.settings.persistence_backend is PersistenceBackend.POSTGRES:
        db_ok = False
        try:
            async with container.services.uow_factory() as uow:
                await uow.execute_raw("SELECT 1")
                await uow.rollback()
            db_ok = True
        except Exception as exc:
            checks.append(
                {
                    "name": "database",
                    "healthy": False,
                    "critical": True,
                    "error": type(exc).__name__,
                }
            )
            healthy = False
        else:
            checks.append({"name": "database", "healthy": db_ok, "critical": True})
            healthy = healthy and db_ok

    provider_count = len(container.services.providers.configured())
    providers_ok = provider_count > 0
    checks.append(
        {
            "name": "providers",
            "healthy": providers_ok,
            "critical": True,
            "count": provider_count,
        }
    )
    healthy = healthy and providers_ok

    status = "ok" if healthy else "unavailable"
    if not healthy:
        response.status_code = 503
    return HealthResponse(
        status=status,  # type: ignore[arg-type]
        service=container.settings.service_name,
        version=container.settings.version,
        checks=checks,
    )


@router.get("/metrics")
async def metrics(container: Annotated[AppContainer, Depends(get_container)]) -> Response:
    """Expose Prometheus metrics.

    Returns:
        The Prometheus text exposition payload.
    """
    body, content_type = render_metrics(container.services.metrics)
    return Response(content=body, media_type=content_type)


__all__ = ["router"]
