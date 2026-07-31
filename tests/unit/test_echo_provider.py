"""Echo provider contract tests."""

from __future__ import annotations

import pytest

from ai_gateway.application.ports.llm_provider import (
    EmbeddingsRequest,
    ProviderCallContext,
    ProviderChatRequest,
)
from ai_gateway.domain.entities.message import Message
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.infrastructure.providers.echo import EchoProvider


@pytest.fixture
def ctx() -> ProviderCallContext:
    return ProviderCallContext(
        request_id=RequestId("r1"), tenant_id=TenantId("t1"), timeout_seconds=5
    )


@pytest.mark.asyncio
async def test_echo_chat_and_stream(ctx: ProviderCallContext) -> None:
    provider = EchoProvider()
    request = ProviderChatRequest(
        model=ModelRef(ProviderName.ECHO, "echo-1"),
        messages=(Message.user("hello world"),),
        temperature=0,
    )
    response = await provider.chat(request, ctx)
    assert "hello world" in response.content

    chunks = [chunk async for chunk in provider.stream_chat(request, ctx)]
    assert any(chunk.delta for chunk in chunks)
    assert chunks[-1].is_final


@pytest.mark.asyncio
async def test_echo_embeddings_deterministic(ctx: ProviderCallContext) -> None:
    provider = EchoProvider()
    request = EmbeddingsRequest(
        model=ModelRef(ProviderName.ECHO, "echo-embed"), inputs=("alpha", "alpha", "beta")
    )
    response = await provider.embed(request, ctx)
    assert response.vectors[0] == response.vectors[1]
    assert response.vectors[0] != response.vectors[2]
