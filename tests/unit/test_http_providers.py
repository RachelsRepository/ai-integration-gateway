"""HTTP provider adapter tests with respx mocks."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from ai_gateway.application.ports.llm_provider import (
    EmbeddingsRequest,
    ProviderCallContext,
    ProviderChatRequest,
    ToolSchema,
)
from ai_gateway.domain.entities.message import Message, MessageRole, ToolCall
from ai_gateway.domain.errors import ProviderAuthenticationError, UnsupportedCapabilityError
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.infrastructure.providers.anthropic import AnthropicProvider
from ai_gateway.infrastructure.providers.azure_openai import AzureOpenAIProvider
from ai_gateway.infrastructure.providers.bedrock import BedrockProvider, _sign
from ai_gateway.infrastructure.providers.google import GoogleGeminiProvider
from ai_gateway.infrastructure.providers.openai import OpenAIProvider


@pytest.fixture
def ctx() -> ProviderCallContext:
    return ProviderCallContext(
        request_id=RequestId("req-1"), tenant_id=TenantId("tenant-1"), timeout_seconds=5
    )


@pytest.fixture
async def shared_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url="http://mock.test") as client:
        yield client


def _openai_chat_response() -> dict[str, object]:
    return {
        "id": "chat-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello",
                    "tool_calls": [
                        {
                            "id": "tc1",
                            "type": "function",
                            "function": {"name": "calc", "arguments": '{"a":1}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }


@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_embed_stream_health(ctx: ProviderCallContext) -> None:
    base = "http://mock.test/v1"
    respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_chat_response())
    )
    respx.post(f"{base}/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2},
            },
        )
    )
    respx.get(f"{base}/models").mock(return_value=httpx.Response(200, json={"data": []}))

    async with httpx.AsyncClient(base_url=base) as client:
        provider = OpenAIProvider(api_key="sk-test", base_url=base, client=client)
        model = ModelRef(ProviderName.OPENAI, "gpt-4o-mini")
        chat = await provider.chat(
            ProviderChatRequest(model=model, messages=(Message.user("hi"),), temperature=0),
            ctx,
        )
        assert chat.message.content == "Hello"
        assert chat.message.tool_calls

        embed = await provider.embed(
            EmbeddingsRequest(
                model=ModelRef(ProviderName.OPENAI, "text-embedding-3-small"), inputs=("a",)
            ),
            ctx,
        )
        assert embed.vectors[0][0] == pytest.approx(0.1)
        assert await provider.health_check() is ProviderStatus.HEALTHY
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_openai_stream_and_errors(ctx: ProviderCallContext) -> None:
    base = "http://mock.test/v1"
    sse = (
        'data: {"choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n'
        'data: {"choices":[{"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
    )
    route = respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    async with httpx.AsyncClient(base_url=base) as client:
        provider = OpenAIProvider(
            api_key="sk-test", base_url=base, client=client, organization="org-1"
        )
        model = ModelRef(ProviderName.OPENAI, "gpt-4o-mini")
        chunks = [
            c
            async for c in provider.stream_chat(
                ProviderChatRequest(
                    model=model,
                    messages=(Message.user("hi"),),
                    temperature=0,
                    tools=(ToolSchema(name="calc", description="calc"),),
                    tool_choice="auto",
                    response_format="json_object",
                    seed=42,
                    stop=("END",),
                    top_p=0.9,
                ),
                ctx,
            )
        ]
        assert any(c.delta for c in chunks)
        assert route.called

    respx.post(f"{base}/chat/completions").mock(return_value=httpx.Response(401, json={}))
    async with httpx.AsyncClient(base_url=base) as client:
        provider = OpenAIProvider(api_key="bad", base_url=base, client=client)
        with pytest.raises(ProviderAuthenticationError):
            await provider.chat(
                ProviderChatRequest(
                    model=ModelRef(ProviderName.OPENAI, "gpt-4o-mini"),
                    messages=(Message.user("x"),),
                    temperature=0,
                ),
                ctx,
            )


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_chat_stream_health(ctx: ProviderCallContext) -> None:
    base = "http://mock.test/v1"
    sse = (
        'data: {"type":"message_start","message":{"usage":{"input_tokens":4}}}\n\n'
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n'
    )
    respx.post(f"{base}/messages").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "id": "msg-1",
                    "content": [
                        {"type": "text", "text": "Hi"},
                        {"type": "tool_use", "id": "t1", "name": "calc", "input": {"x": 1}},
                    ],
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                    "stop_reason": "end_turn",
                },
            ),
            httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}),
        ]
    )
    respx.get(f"{base}/models").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient(base_url=base) as client:
        provider = AnthropicProvider(api_key="ant-test", base_url=base, client=client)
        model = ModelRef(ProviderName.ANTHROPIC, "claude-3-5-haiku-latest")
        request = ProviderChatRequest(
            model=model,
            messages=(
                Message.system("sys"),
                Message(
                    role=MessageRole.ASSISTANT,
                    content="",
                    tool_calls=(ToolCall(id="t0", name="search", arguments={"q": "x"}),),
                ),
                Message.tool_result(tool_call_id="t0", name="search", content="result"),
            ),
            temperature=0,
            tools=(ToolSchema(name="calc", description="calc"),),
        )
        chat = await provider.chat(request, ctx)
        assert chat.message.content or chat.message.tool_calls

        with pytest.raises(UnsupportedCapabilityError):
            await provider.embed(EmbeddingsRequest(model=model, inputs=("a",)), ctx)

        stream_req = ProviderChatRequest(
            model=model, messages=(Message.user("stream"),), temperature=0
        )
        chunks = [c async for c in provider.stream_chat(stream_req, ctx)]
        assert chunks

        assert await provider.health_check() is ProviderStatus.HEALTHY


@pytest.mark.asyncio
@respx.mock
async def test_google_chat_stream_embed(ctx: ProviderCallContext) -> None:
    base = "http://mock.test/v1beta"
    respx.post(url__regex=rf"{base}/models/gemini-1.5-flash:generateContent").mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Hello"},
                                {"functionCall": {"name": "calc", "args": {"a": 1}}},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
            },
        )
    )
    sse = (
        'data: {"candidates":[{"content":{"parts":[{"text":"Hi"}]},"finishReason":"STOP"}],'
        '"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":1}}\n\n'
    )
    respx.post(url__regex=rf"{base}/models/gemini-1.5-flash:streamGenerateContent").mock(
        return_value=httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    respx.post(url__regex=rf"{base}/models/text-embedding-004:embedContent").mock(
        return_value=httpx.Response(200, json={"embedding": {"values": [0.5, 0.6]}})
    )
    respx.get(f"{base}/models").mock(return_value=httpx.Response(200, json={"models": []}))

    async with httpx.AsyncClient(base_url=base) as client:
        provider = GoogleGeminiProvider(api_key="g-test", base_url=base, client=client)
        model = ModelRef(ProviderName.GOOGLE, "gemini-1.5-flash")
        chat = await provider.chat(
            ProviderChatRequest(
                model=model,
                messages=(Message.system("sys"), Message.user("hi")),
                temperature=0,
                tools=(ToolSchema(name="calc", description="calc"),),
            ),
            ctx,
        )
        assert chat.message.content

        chunks = [
            c
            async for c in provider.stream_chat(
                ProviderChatRequest(model=model, messages=(Message.user("s"),), temperature=0),
                ctx,
            )
        ]
        assert chunks

        embed = await provider.embed(
            EmbeddingsRequest(
                model=ModelRef(ProviderName.GOOGLE, "text-embedding-004"), inputs=("hello",)
            ),
            ctx,
        )
        assert embed.vectors[0][0] == pytest.approx(0.5)
        assert await provider.health_check() is ProviderStatus.HEALTHY


@pytest.mark.asyncio
@respx.mock
async def test_azure_openai_chat_stream_embed(ctx: ProviderCallContext) -> None:
    endpoint = "http://mock.test"
    deployment = "gpt-4o-mini"
    version = "2024-06-01"
    chat_url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={version}"
    embed_url = (
        f"{endpoint}/openai/deployments/text-embedding-3-small/embeddings?api-version={version}"
    )
    respx.post(chat_url).mock(return_value=httpx.Response(200, json=_openai_chat_response()))
    sse = 'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}\n\n'
    respx.post(chat_url).mock(
        side_effect=[
            httpx.Response(200, json=_openai_chat_response()),
            httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}),
        ]
    )
    respx.post(embed_url).mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.3]}], "usage": {"prompt_tokens": 1}},
        )
    )
    respx.get(f"{endpoint}/openai/models?api-version={version}").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    async with httpx.AsyncClient(base_url=endpoint) as client:
        provider = AzureOpenAIProvider(
            api_key="azure-key",
            endpoint=endpoint,
            api_version=version,
            deployments={"gpt-4o-mini": deployment},
            client=client,
        )
        model = ModelRef(ProviderName.AZURE_OPENAI, "gpt-4o-mini")
        chat = await provider.chat(
            ProviderChatRequest(
                model=model,
                messages=(Message.user("hi"),),
                temperature=0,
                tools=(ToolSchema(name="calc", description="calc"),),
            ),
            ctx,
        )
        assert chat.message.content

        chunks = [
            c
            async for c in provider.stream_chat(
                ProviderChatRequest(model=model, messages=(Message.user("s"),), temperature=0),
                ctx,
            )
        ]
        assert chunks

        embed = await provider.embed(
            EmbeddingsRequest(
                model=ModelRef(ProviderName.AZURE_OPENAI, "text-embedding-3-small"), inputs=("a",)
            ),
            ctx,
        )
        assert embed.vectors
        assert await provider.health_check() is ProviderStatus.HEALTHY


def test_azure_requires_endpoint() -> None:
    with pytest.raises(Exception):
        AzureOpenAIProvider(api_key="k", endpoint="")


@pytest.mark.asyncio
@respx.mock
async def test_bedrock_chat_stream(ctx: ProviderCallContext) -> None:
    model_id = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    url = f"http://mock.test/model/{model_id}/invoke"
    respx.post(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Bedrock says hi"}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
                "stop_reason": "end_turn",
            },
        )
    )

    async with httpx.AsyncClient(base_url="http://mock.test") as client:
        provider = BedrockProvider(
            access_key_id="AKIA",
            secret_access_key="secret",
            session_token="token",
            endpoint="http://mock.test",
            client=client,
        )
        model = ModelRef(ProviderName.BEDROCK, model_id)
        chat = await provider.chat(
            ProviderChatRequest(
                model=model,
                messages=(Message.system("sys"), Message.user("hi")),
                temperature=0,
            ),
            ctx,
        )
        assert "Bedrock" in chat.message.content

        chunks = [
            c
            async for c in provider.stream_chat(
                ProviderChatRequest(model=model, messages=(Message.user("hi"),), temperature=0),
                ctx,
            )
        ]
        assert len(chunks) >= 2

        with pytest.raises(UnsupportedCapabilityError):
            await provider.embed(EmbeddingsRequest(model=model, inputs=("a",)), ctx)

        assert await provider.health_check() is ProviderStatus.HEALTHY


def test_bedrock_sign_includes_session_token() -> None:
    headers = _sign(
        method="POST",
        url="https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke",
        headers={},
        payload=b"{}",
        access_key="AKIA",
        secret_key="secret",
        session_token="sess",
        region="us-east-1",
    )
    assert "Authorization" in headers
    assert headers["X-Amz-Security-Token"] == "sess"
