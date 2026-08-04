"""FastAPI dependency injection."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from ai_gateway.application.dto import RequestContext
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.container import AppContainer
from ai_gateway.domain.value_objects.identifiers import RequestId
from ai_gateway.observability.correlation import new_request_id


def get_container(request: Request) -> AppContainer:
    """Return the application container.

    Args:
        request: Current request.

    Returns:
        The container.
    """
    container: AppContainer = request.app.state.container
    return container


async def get_request_context(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    x_idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    x_scenario: Annotated[str | None, Header(alias="X-Scenario")] = None,
) -> RequestContext:
    """Authenticate the caller and build a request context.

    Args:
        request: Current request.
        container: Application container.
        authorization: Bearer token header.
        x_api_key: API key header.
        x_request_id: Optional caller-supplied request identifier.
        x_idempotency_key: Optional idempotency key.
        x_scenario: Local/test-only provider scenario override.

    Returns:
        The populated request context.
    """
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    async with container.services.uow_factory() as uow:
        principal, tenant = await container.authenticator.authenticate(
            uow, api_key=x_api_key, bearer_token=bearer
        )
        await uow.commit()
    scenario = None
    if x_scenario and container.settings.provider_scenario_forwarding:
        scenario = x_scenario.strip().lower() or None
    return RequestContext(
        principal=principal,
        tenant=tenant,
        request_id=RequestId(x_request_id or new_request_id()),
        source_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        idempotency_key=x_idempotency_key,
        deadline_seconds=container.services.default_timeout_seconds,
        provider_scenario=scenario,
    )


def get_services(
    container: Annotated[AppContainer, Depends(get_container)],
) -> GatewayServices:
    """Return the shared gateway services."""
    return container.services


__all__ = [
    "AppContainer",
    "get_container",
    "get_request_context",
    "get_services",
]
