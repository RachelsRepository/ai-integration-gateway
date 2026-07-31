"""Embeddings routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ai_gateway.api.deps import get_request_context, get_services
from ai_gateway.api.mappers import embeddings_result_to_response, to_embeddings_command
from ai_gateway.api.schemas import EmbeddingsRequest
from ai_gateway.application.dto import RequestContext
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.embeddings import EmbeddingsUseCase

router = APIRouter(prefix="/v1", tags=["embeddings"])


@router.post("/embeddings")
async def create_embeddings(
    body: EmbeddingsRequest,
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
) -> dict[str, Any]:
    """Compute embeddings for one or more inputs."""
    result = await EmbeddingsUseCase(services).execute(to_embeddings_command(body), context)
    return embeddings_result_to_response(result)


__all__ = ["router"]
