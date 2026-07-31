"""Provider contract tests against the shared LLMProvider protocol."""

from __future__ import annotations

import pytest

from ai_gateway.application.ports.llm_provider import (
    EmbeddingsRequest,
    LLMProvider,
    ProviderCallContext,
    ProviderChatRequest,
)
from ai_gateway.domain.entities.message import Message
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.model import ModelCapability
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.infrastructure.providers.echo import EchoProvider


@pytest.mark.contract
@pytest.mark.asyncio
async def test_echo_satisfies_provider_contract() -> None:
    provider: LLMProvider = EchoProvider()
    assert provider.name is ProviderName.ECHO
    assert provider.supported_models()
    assert await provider.health_check() is ProviderStatus.HEALTHY
    ctx = ProviderCallContext(request_id=RequestId("r"), tenant_id=TenantId("t"))
    chat_model = next(
        m.ref for m in provider.supported_models() if m.supports(ModelCapability.CHAT)
    )
    response = await provider.chat(
        ProviderChatRequest(model=chat_model, messages=(Message.user("contract"),)),
        ctx,
    )
    assert response.message.content
    embed_model = next(
        m.ref for m in provider.supported_models() if m.supports(ModelCapability.EMBEDDINGS)
    )
    embeddings = await provider.embed(EmbeddingsRequest(model=embed_model, inputs=("x",)), ctx)
    assert embeddings.vectors
    await provider.aclose()
