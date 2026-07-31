"""Google Gemini provider adapter."""

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
from ai_gateway.domain.value_objects.model import ModelSpec
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.providers.base import (
    map_http_error,
    map_transport_error,
    parse_finish_reason,
    require_mapping,
    split_system_messages,
)
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog


class GoogleGeminiProvider:
    """Adapter for the Gemini generateContent API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        catalog: StaticModelCatalog | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise the adapter."""
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        catalog = catalog or StaticModelCatalog()
        self._models = tuple(catalog.for_provider(ProviderName.GOOGLE))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=60.0)

    @property
    def name(self) -> ProviderName:
        """Return the provider identifier."""
        return ProviderName.GOOGLE

    def supported_models(self) -> tuple[ModelSpec, ...]:
        """Return supported models."""
        return self._models

    async def chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> ProviderChatResponse:
        """Execute generateContent."""
        started = time.perf_counter()
        payload = self._payload(request)
        data = await self._post(f"/models/{request.model.name}:generateContent", payload, context)
        candidate = (data.get("candidates") or [{}])[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            if "text" in part:
                text_parts.append(str(part["text"]))
            if "functionCall" in part:
                call = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        id=f"gemini_tool_{index}",
                        name=str(call.get("name") or ""),
                        arguments=dict(call.get("args") or {}),
                    )
                )
        usage_raw = data.get("usageMetadata") or {}
        return ProviderChatResponse(
            model=request.model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content="".join(text_parts),
                tool_calls=tuple(tool_calls),
            ),
            usage=TokenUsage(
                prompt_tokens=int(usage_raw.get("promptTokenCount") or 0),
                completion_tokens=int(usage_raw.get("candidatesTokenCount") or 0),
            ),
            finish_reason=parse_finish_reason(candidate.get("finishReason")),
            raw_finish_reason=candidate.get("finishReason"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream_chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> AsyncIterator[StreamChunk]:
        """Stream generateContent."""
        payload = self._payload(request)
        url = (
            f"{self._base_url}/models/{request.model.name}:streamGenerateContent"
            f"?alt=sse&key={self._api_key}"
        )
        index = 0
        try:
            async with self._client.stream(
                "POST",
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=context.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise map_http_error(self.name, response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    candidate = (event.get("candidates") or [{}])[0]
                    parts = ((candidate.get("content") or {}).get("parts")) or []
                    for part in parts:
                        if "text" in part:
                            yield StreamChunk(delta=str(part["text"]), index=index)
                            index += 1
                    finish = candidate.get("finishReason")
                    if finish:
                        usage_raw = event.get("usageMetadata") or {}
                        yield StreamChunk(
                            finish_reason=parse_finish_reason(finish),
                            usage=TokenUsage(
                                prompt_tokens=int(usage_raw.get("promptTokenCount") or 0),
                                completion_tokens=int(usage_raw.get("candidatesTokenCount") or 0),
                            ),
                            index=index,
                        )
        except httpx.HTTPError as exc:
            raise map_transport_error(self.name, exc) from exc

    async def embed(
        self, request: EmbeddingsRequest, context: ProviderCallContext
    ) -> EmbeddingsResponse:
        """Compute embeddings via embedContent."""
        started = time.perf_counter()
        vectors: list[tuple[float, ...]] = []
        total_tokens = 0
        for text in request.inputs:
            data = await self._post(
                f"/models/{request.model.name}:embedContent",
                {"content": {"parts": [{"text": text}]}},
                context,
            )
            values = (data.get("embedding") or {}).get("values") or []
            vectors.append(tuple(float(v) for v in values))
            total_tokens += max(len(text) // 4, 1)
        return EmbeddingsResponse(
            model=request.model,
            vectors=tuple(vectors),
            usage=TokenUsage(prompt_tokens=total_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> ProviderStatus:
        """Probe the models list endpoint."""
        try:
            response = await self._client.get(
                f"{self._base_url}/models",
                params={"key": self._api_key},
                timeout=5.0,
            )
            return ProviderStatus.HEALTHY if response.status_code < 500 else ProviderStatus.DEGRADED
        except httpx.HTTPError:
            return ProviderStatus.UNAVAILABLE

    async def aclose(self) -> None:
        """Close the HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()

    def _payload(self, request: ProviderChatRequest) -> dict[str, Any]:
        system, rest = split_system_messages(request.messages)
        contents: list[dict[str, Any]] = []
        for message in rest:
            role = "user" if message.role in {MessageRole.USER, MessageRole.TOOL} else "model"
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"text": message.content})
            for call in message.tool_calls:
                parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
            contents.append({"role": role, "parts": parts or [{"text": ""}]})
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters or {"type": "object"},
                        }
                        for tool in request.tools
                    ]
                }
            ]
        return payload

    async def _post(
        self, path: str, payload: dict[str, Any], context: ProviderCallContext
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._base_url}{path}",
                params={"key": self._api_key},
                json=payload,
                timeout=context.timeout_seconds,
            )
            if response.status_code >= 400:
                raise map_http_error(self.name, response)
            return dict(require_mapping(response.json(), self.name))
        except httpx.HTTPError as exc:
            raise map_transport_error(self.name, exc) from exc


__all__ = ["GoogleGeminiProvider"]
