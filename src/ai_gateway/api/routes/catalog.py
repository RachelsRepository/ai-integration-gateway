"""Model and provider discovery routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ai_gateway.api.deps import get_request_context, get_services
from ai_gateway.api.schemas import ModelResponse, ProviderResponse
from ai_gateway.application.dto import RequestContext
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.catalog import ListModelsUseCase, ListProvidersUseCase
from ai_gateway.domain.value_objects.model import ModelCapability

router = APIRouter(prefix="/v1", tags=["catalog"])


@router.get("/models", response_model=list[ModelResponse])
async def list_models(
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
    capability: Annotated[str | None, Query()] = None,
) -> list[ModelResponse]:
    """List models the caller is entitled to use."""
    cap = ModelCapability(capability) if capability else None
    views = ListModelsUseCase(services).execute(context, capability=cap)
    return [
        ModelResponse(
            id=view.id,
            provider=view.provider.value,
            capabilities=[c.value for c in view.capabilities],
            context_window=view.context_window,
            max_output_tokens=view.max_output_tokens,
            input_cost_per_1k=str(view.input_cost_per_1k.amount),
            output_cost_per_1k=str(view.output_cost_per_1k.amount),
            tier=view.tier,
            expected_latency_ms=view.expected_latency_ms,
            available=view.available,
            deprecated=view.deprecated,
        )
        for view in views
    ]


@router.get("/providers", response_model=list[ProviderResponse])
async def list_providers(
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
) -> list[ProviderResponse]:
    """List configured providers and their live health."""
    views = ListProvidersUseCase(services).execute(context)
    return [
        ProviderResponse(
            name=view.name.value,
            status=view.status.value,
            circuit_state=view.circuit_state,
            models=list(view.models),
            observed_latency_ms=view.observed_latency_ms,
            error_rate=view.error_rate,
            enabled_for_tenant=view.enabled_for_tenant,
        )
        for view in views
    ]


__all__ = ["router"]
