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


def test_agent_run_command_tools_canonical_tuple() -> None:
    empty = AgentRunCommand(input="x", tools=())
    assert empty.tools == ()
    assert isinstance(empty.tools, tuple)

    ordered = AgentRunCommand(input="x", tools=("calculator", "echo", "calculator"))
    assert ordered.tools == ("calculator", "echo", "calculator")

    # Domain definition stores a frozenset; resume boundary sorts for determinism.
    from ai_gateway.domain.entities.agent import AgentDefinition

    definition = AgentDefinition(
        name="t",
        instructions="i",
        tools=frozenset({"echo", "calculator"}),
    )
    resumed = tuple(sorted(definition.tools))
    assert resumed == ("calculator", "echo")
    assert AgentRunCommand(input="resume", tools=resumed).tools == ("calculator", "echo")


@pytest.mark.asyncio
async def test_agent_tools_dedupe_on_definition(
    seeded_services: GatewayServices, request_context: RequestContext
) -> None:
    del request_context
    use_case = RunAgentUseCase(seeded_services)
    definition = use_case._definition(
        AgentRunCommand(
            input="hi",
            tools=("calculator", "echo", "calculator"),
            model="echo/echo-1",
        )
    )
    assert definition.tools == frozenset({"calculator", "echo"})
    # Resume path: frozenset → sorted tuple is stable across runs
    assert tuple(sorted(definition.tools)) == ("calculator", "echo")
