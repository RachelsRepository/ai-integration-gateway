"""Chat completion use case tests."""

from __future__ import annotations

import pytest

from ai_gateway.application.dto import ChatCompletionCommand, RequestContext
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.chat_completion import ChatCompletionUseCase
from ai_gateway.domain.entities.message import Message
from ai_gateway.domain.entities.tenant import Tenant


@pytest.mark.asyncio
async def test_chat_completion_happy_path(
    seeded_services: GatewayServices, request_context: RequestContext
) -> None:
    use_case = ChatCompletionUseCase(seeded_services)
    result = await use_case.execute(
        ChatCompletionCommand(
            messages=(Message.user("ping"),),
            model="echo/echo-1",
            temperature=0,
            cache=False,
        ),
        request_context,
    )
    assert "ping" in result.content
    assert result.model.qualified == "echo/echo-1"
    assert result.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_chat_completion_cache_hit(
    seeded_services: GatewayServices, request_context: RequestContext
) -> None:
    use_case = ChatCompletionUseCase(seeded_services)
    command = ChatCompletionCommand(
        messages=(Message.user("cache-me"),),
        model="echo/echo-1",
        temperature=0,
        cache=True,
    )
    first = await use_case.execute(command, request_context)
    second = await use_case.execute(command, request_context)
    assert first.content == second.content
    assert second.cached is True


@pytest.mark.asyncio
async def test_prompt_managed_chat(
    seeded_services: GatewayServices, request_context: RequestContext, tenant: Tenant
) -> None:
    from ai_gateway.application.dto import PromptPublishCommand
    from ai_gateway.application.use_cases.prompts import PublishPromptUseCase

    await PublishPromptUseCase(seeded_services).execute(
        PromptPublishCommand(
            name="greet",
            template="Hello {{ name }}",
            system_prompt="You are concise",
        ),
        request_context,
    )
    result = await ChatCompletionUseCase(seeded_services).execute(
        ChatCompletionCommand(
            prompt_name="greet",
            prompt_variables={"name": "Ada"},
            model="echo/echo-1",
            temperature=0,
            cache=False,
        ),
        request_context,
    )
    assert "Ada" in result.content
