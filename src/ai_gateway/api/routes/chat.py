"""Chat completion routes."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ai_gateway.api.deps import get_request_context, get_services
from ai_gateway.api.mappers import chat_result_to_response, to_chat_command
from ai_gateway.api.schemas import ChatCompletionRequest
from ai_gateway.application.dto import RequestContext, StreamEventType
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.chat_completion import ChatCompletionUseCase

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    body: ChatCompletionRequest,
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
) -> Any:
    """Generate a chat completion, optionally streamed as SSE.

    Args:
        body: Request body.
        context: Authenticated request context.
        services: Shared gateway services.

    Returns:
        A JSON completion or an SSE stream.
    """
    command = to_chat_command(body)
    use_case = ChatCompletionUseCase(services)
    if command.stream:
        return StreamingResponse(
            _sse(use_case, command, context),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Request-ID": str(context.request_id),
            },
        )
    result = await use_case.execute(command, context)
    return chat_result_to_response(result)


@router.post("/responses", response_model=None)
async def responses(
    body: ChatCompletionRequest,
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
) -> Any:
    """Alias of chat completions for clients using a responses-style path."""
    return await chat_completions(body, context, services)


async def _sse(
    use_case: ChatCompletionUseCase,
    command: Any,
    context: RequestContext,
) -> Any:
    async for event in use_case.stream(command, context):
        payload = json.dumps({"type": event.type.value, "data": event.data, "index": event.index})
        yield f"event: {event.type.value}\ndata: {payload}\n\n"
        if event.type in {StreamEventType.DONE, StreamEventType.ERROR}:
            break
    yield "data: [DONE]\n\n"


__all__ = ["router"]
