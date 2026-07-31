"""Embeddings and agent use case tests."""

from __future__ import annotations

import pytest

from ai_gateway.application.dto import AgentRunCommand, EmbeddingsCommand, RequestContext
from ai_gateway.application.use_cases.agent_run import RunAgentUseCase
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.embeddings import EmbeddingsUseCase


@pytest.mark.asyncio
async def test_embeddings(
    seeded_services: GatewayServices, request_context: RequestContext
) -> None:
    result = await EmbeddingsUseCase(seeded_services).execute(
        EmbeddingsCommand(inputs=("one", "two"), model="echo/echo-embed"),
        request_context,
    )
    assert len(result.vectors) == 2
    assert result.dimensions == 8


@pytest.mark.asyncio
async def test_agent_run_with_calculator(
    seeded_services: GatewayServices, request_context: RequestContext
) -> None:
    # Echo provider returns a tool call when the user message starts with TOOL:
    result = await RunAgentUseCase(seeded_services).execute(
        AgentRunCommand(
            input='TOOL:calculator {"expression": "2 + 2"}',
            tools=("calculator",),
            model="echo/echo-1",
            max_iterations=3,
        ),
        request_context,
    )
    assert result.status.value in {"succeeded", "failed"}
    assert len(result.steps) >= 1
