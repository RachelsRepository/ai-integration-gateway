"""Agent execution routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ai_gateway.api.deps import get_request_context, get_services
from ai_gateway.api.mappers import to_agent_command, usage_schema
from ai_gateway.api.schemas import AgentRunRequest, AgentRunResponse
from ai_gateway.application.dto import RequestContext
from ai_gateway.application.use_cases.agent_run import RunAgentUseCase
from ai_gateway.application.use_cases.base import GatewayServices

router = APIRouter(prefix="/v1", tags=["agents"])


@router.post("/agents/run", response_model=AgentRunResponse)
async def run_agent(
    body: AgentRunRequest,
    context: Annotated[RequestContext, Depends(get_request_context)],
    services: Annotated[GatewayServices, Depends(get_services)],
) -> AgentRunResponse:
    """Execute an agent until it answers or exhausts its budget."""
    result = await RunAgentUseCase(services).execute(to_agent_command(body), context)
    steps: list[dict[str, Any]] = [
        {
            "index": step.index,
            "type": step.type.value,
            "content": step.content,
            "duration_ms": step.duration_ms,
            "usage": usage_schema(step.usage).model_dump(),
            "cost_micros": step.cost.micros,
            "error": step.error,
            "tool_invocations": [
                {
                    "name": inv.call.name,
                    "succeeded": inv.succeeded,
                    "duration_ms": inv.duration_ms,
                    "error": inv.error,
                }
                for inv in step.tool_invocations
            ],
        }
        for step in result.steps
    ]
    return AgentRunResponse(
        run_id=result.run_id,
        request_id=result.request_id,
        status=result.status.value,
        output=result.output,
        steps=steps,
        usage=usage_schema(result.usage),
        cost_micros=result.cost.micros,
        currency=result.cost.currency,
        latency_ms=result.latency_ms,
        conversation_id=result.conversation_id,
        error=result.error,
    )


__all__ = ["router"]
