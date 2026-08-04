"""Anthropic Messages API provider adapter."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ai_gateway.application.ports.llm_provider import (
    EmbeddingsRequest,
    EmbeddingsResponse,
    ProviderCallContext,
    ProviderChatRequest,
    ProviderChatResponse,
    StreamChunk,
)
from ai_gateway.domain.entities.message import Message, MessageRole, ToolCall
from ai_gateway.domain.errors import UnsupportedCapabilityError
from ai_gateway.domain.value_objects.model import ModelSpec
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.providers.base import (
    iter_sse_lines,
    map_http_error,
    map_transport_error,
    parse_finish_reason,
    require_mapping,
    split_system_messages,
)
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog


class AnthropicProvider:
    """Adapter for the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1",
        version: str = "2023-06-01",
        catalog: StaticModelCatalog | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise the adapter."""
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._version = version
        catalog = catalog or StaticModelCatalog()
        self._models = tuple(catalog.for_provider(ProviderName.ANTHROPIC))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=60.0)

    @property
    def name(self) -> ProviderName:
        """Return the provider identifier."""
        return ProviderName.ANTHROPIC

    def supported_models(self) -> tuple[ModelSpec, ...]:
        """Return supported models."""
        return self._models

    async def chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> ProviderChatResponse:
        """Execute a messages request."""
        started = time.perf_counter()
        payload = self._payload(request, stream=False)
        data = await self._post("/messages", payload, context)
        content_blocks = data.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=dict(block.get("input") or {}),
                    )
                )
        usage_raw = data.get("usage") or {}
        return ProviderChatResponse(
            model=request.model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content="".join(text_parts),
                tool_calls=tuple(tool_calls),
            ),
            usage=TokenUsage(
                prompt_tokens=int(usage_raw.get("input_tokens") or 0),
                completion_tokens=int(usage_raw.get("output_tokens") or 0),
            ),
            finish_reason=parse_finish_reason(data.get("stop_reason")),
            provider_request_id=data.get("id"),
            raw_finish_reason=data.get("stop_reason"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream_chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> AsyncIterator[StreamChunk]:
        """Stream a messages request."""
        payload = self._payload(request, stream=True)
        index = 0
        usage = TokenUsage.empty()
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/messages",
                json=payload,
                headers=self._headers(context),
                timeout=context.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise map_http_error(self.name, response)
                async for data in iter_sse_lines(response):
                    event = json.loads(data)
                    etype = event.get("type")
                    if etype == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield StreamChunk(delta=str(delta.get("text") or ""), index=index)
                            index += 1
                    elif etype == "message_delta":
                        usage_raw = event.get("usage") or {}
                        usage = TokenUsage(
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=int(usage_raw.get("output_tokens") or 0),
                        )
                        stop = (event.get("delta") or {}).get("stop_reason")
                        if stop:
                            yield StreamChunk(
                                finish_reason=parse_finish_reason(stop),
                                usage=usage,
                                index=index,
                            )
                    elif etype == "message_start":
                        msg = event.get("message") or {}
                        usage_raw = msg.get("usage") or {}
                        usage = TokenUsage(prompt_tokens=int(usage_raw.get("input_tokens") or 0))
        except httpx.HTTPError as exc:
            raise map_transport_error(self.name, exc) from exc

    async def embed(
        self, request: EmbeddingsRequest, context: ProviderCallContext
    ) -> EmbeddingsResponse:
        """Anthropic does not expose a public embeddings API via this adapter."""
        del request, context
        raise UnsupportedCapabilityError("Anthropic adapter does not support embeddings")

    async def health_check(self) -> ProviderStatus:
        """Best-effort health probe."""
        try:
            response = await self._client.get(
                f"{self._base_url}/models",
                headers=self._headers_bare(),
                timeout=5.0,
            )
            if response.status_code in {401, 403, 404}:
                return ProviderStatus.HEALTHY
            return ProviderStatus.HEALTHY if response.status_code < 500 else ProviderStatus.DEGRADED
        except httpx.HTTPError:
            return ProviderStatus.UNAVAILABLE

    async def aclose(self) -> None:
        """Close the HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()

    def _payload(self, request: ProviderChatRequest, *, stream: bool) -> dict[str, Any]:
        system, rest = split_system_messages(request.messages)
        messages: list[dict[str, Any]] = []
        for message in rest:
            if message.role is MessageRole.TOOL:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id,
                                "content": message.content,
                            }
                        ],
                    }
                )
                continue
            if message.tool_calls:
                content: list[dict[str, Any]] = []
                if message.content:
                    content.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                messages.append({"role": "assistant", "content": content})
                continue
            messages.append({"role": message.role.value, "content": message.content})
        payload: dict[str, Any] = {
            "model": request.model.name,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters or {"type": "object", "properties": {}},
                }
                for tool in request.tools
            ]
        return payload

    def _headers(self, context: ProviderCallContext) -> dict[str, str]:
        headers = self._headers_bare()
        headers["X-Request-ID"] = str(context.request_id)
        headers.update(context.extra_headers)
        return headers

    def _headers_bare(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._version,
            "content-type": "application/json",
        }

    async def _post(
        self, path: str, payload: dict[str, Any], context: ProviderCallContext
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._base_url}{path}",
                json=payload,
                headers=self._headers(context),
                timeout=context.timeout_seconds,
            )
            if response.status_code >= 400:
                raise map_http_error(self.name, response)
            return dict(require_mapping(response.json(), self.name))
        except httpx.HTTPError as exc:
            raise map_transport_error(self.name, exc) from exc


__all__ = ["AnthropicProvider"]
