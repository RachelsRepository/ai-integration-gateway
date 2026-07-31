"""Streaming use case tests."""

from __future__ import annotations

import pytest

from ai_gateway.application.dto import ChatCompletionCommand, RequestContext, StreamEventType
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.chat_completion import ChatCompletionUseCase
from ai_gateway.domain.entities.message import Message


@pytest.mark.asyncio
async def test_stream_emits_start_delta_and_done(
    seeded_services: GatewayServices, request_context: RequestContext
) -> None:
    use_case = ChatCompletionUseCase(seeded_services)
    events = [
        event
        async for event in use_case.stream(
            ChatCompletionCommand(
                messages=(Message.user("stream please"),),
                model="echo/echo-1",
                stream=True,
                temperature=0,
            ),
            request_context,
        )
    ]
    types = [event.type for event in events]
    assert StreamEventType.START in types
    assert StreamEventType.DELTA in types
    assert StreamEventType.DONE in types
