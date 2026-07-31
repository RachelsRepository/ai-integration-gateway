"""Prompt management routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ai_gateway.api.deps import get_request_context, get_services
from ai_gateway.api.mappers import to_prompt_command
from ai_gateway.api.schemas import PromptPublishRequest, PromptResponse
from ai_gateway.application.dto import RequestContext
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.prompts import (
    GetPromptUseCase,
    ListPromptsUseCase,
    PublishPromptUseCase,
)

router = APIRouter(prefix="/v1", tags=["prompts"])


def _to_response(view: Any) -> PromptResponse:
    return PromptResponse(
        id=view.id,
        name=view.name,
        description=view.description,
        active_version=view.active_version,
        versions=[
            {
                "version": version.version,
                "template": version.template,
                "system_prompt": version.system_prompt,
                "safety_prompt": version.safety_prompt,
                "required_variables": list(version.required_variables),
                "created_at": version.created_at.isoformat(),
                "created_by": version.created_by,
                "notes": version.notes,
            }
            for version in view.versions
        ],
        labels=view.labels,
        updated_at=view.updated_at,
    )


@router.post("/prompts", response_model=PromptResponse)
async def publish_prompt(
    body: PromptPublishRequest,
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
) -> PromptResponse:
    """Create a prompt or publish a new immutable version."""
    view = await PublishPromptUseCase(services).execute(to_prompt_command(body), context)
    return _to_response(view)


@router.get("/prompts", response_model=list[PromptResponse])
async def list_prompts(
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PromptResponse]:
    """List the tenant's managed prompts."""
    views = await ListPromptsUseCase(services).execute(context, limit=limit, offset=offset)
    return [_to_response(view) for view in views]


@router.get("/prompts/{name}", response_model=PromptResponse)
async def get_prompt(
    name: str,
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
) -> PromptResponse:
    """Fetch a managed prompt and its version history."""
    view = await GetPromptUseCase(services).execute(name, context)
    return _to_response(view)


__all__ = ["router"]
