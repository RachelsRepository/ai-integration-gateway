"""OpenAI HTTP provider adapter."""

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
from ai_gateway.domain.entities.message import Message, MessageRole
from ai_gateway.domain.value_objects.model import ModelSpec
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.providers.base import (
    iter_sse_lines,
    map_http_error,
    map_transport_error,
    messages_to_openai,
    parse_finish_reason,
    parse_openai_tool_calls,
    require_mapping,
    tools_to_openai,
)
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog


class OpenAIProvider:
    """Adapter for the OpenAI Chat Completions and Embeddings APIs."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        organization: str | None = None,
        catalog: StaticModelCatalog | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise the adapter.

        Args:
            api_key: OpenAI API key.
            base_url: API base URL.
            organization: Optional organization header.
            catalog: Model catalogue.
            client: Optional shared HTTP client.
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._organization = organization
        catalog = catalog or StaticModelCatalog()
        self._models = tuple(catalog.for_provider(ProviderName.OPENAI))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=60.0)

    @property
    def name(self) -> ProviderName:
        """Return the provider identifier."""
        return ProviderName.OPENAI

    def supported_models(self) -> tuple[ModelSpec, ...]:
        """Return the catalogue entries this adapter can serve."""
        return self._models

    async def chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> ProviderChatResponse:
        """Execute a chat completion.

        Args:
            request: Normalised request.
            context: Call context.

        Returns:
            The normalised response.
        """
        started = time.perf_counter()
        payload = self._chat_payload(request, stream=False)
        data = await self._post("/chat/completions", payload, context)
        choice = (data.get("choices") or [{}])[0]
        message_raw = choice.get("message") or {}
        usage_raw = data.get("usage") or {}
        message = Message(
            role=MessageRole.ASSISTANT,
            content=str(message_raw.get("content") or ""),
            tool_calls=parse_openai_tool_calls(message_raw.get("tool_calls")),
        )
        return ProviderChatResponse(
            model=request.model,
            message=message,
            usage=TokenUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            ),
            finish_reason=parse_finish_reason(choice.get("finish_reason")),
            provider_request_id=data.get("id"),
            raw_finish_reason=choice.get("finish_reason"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream_chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion.

        Args:
            request: Normalised request.
            context: Call context.

        Yields:
            Incremental chunks.
        """
        payload = self._chat_payload(request, stream=True)
        headers = self._headers(context)
        index = 0
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=context.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise map_http_error(self.name, response)
                async for data in iter_sse_lines(response):
                    chunk = json.loads(data)
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    content = str(delta.get("content") or "")
                    finish = choice.get("finish_reason")
                    usage_raw = chunk.get("usage")
                    usage = None
                    if usage_raw:
                        usage = TokenUsage(
                            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
                        )
                    tool_delta = None
                    if delta.get("tool_calls"):
                        tool_delta = {"tool_calls": delta["tool_calls"]}
                    yield StreamChunk(
                        delta=content,
                        tool_call_delta=tool_delta,
                        finish_reason=parse_finish_reason(finish) if finish else None,
                        usage=usage,
                        index=index,
                    )
                    index += 1
        except httpx.HTTPError as exc:
            raise map_transport_error(self.name, exc) from exc

    async def embed(
        self, request: EmbeddingsRequest, context: ProviderCallContext
    ) -> EmbeddingsResponse:
        """Compute embeddings.

        Args:
            request: Normalised request.
            context: Call context.

        Returns:
            The normalised response.
        """
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": request.model.name,
            "input": list(request.inputs),
            "encoding_format": request.encoding_format,
        }
        if request.dimensions is not None:
            payload["dimensions"] = request.dimensions
        data = await self._post("/embeddings", payload, context)
        vectors = tuple(
            tuple(float(v) for v in item.get("embedding") or [])
            for item in sorted(data.get("data") or [], key=lambda i: i.get("index", 0))
        )
        usage_raw = data.get("usage") or {}
        return EmbeddingsResponse(
            model=request.model,
            vectors=vectors,
            usage=TokenUsage(prompt_tokens=int(usage_raw.get("prompt_tokens") or 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> ProviderStatus:
        """Probe the models endpoint."""
        try:
            response = await self._client.get(
                f"{self._base_url}/models",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=5.0,
            )
            return ProviderStatus.HEALTHY if response.status_code < 500 else ProviderStatus.DEGRADED
        except httpx.HTTPError:
            return ProviderStatus.UNAVAILABLE

    async def aclose(self) -> None:
        """Close the HTTP client when owned by this adapter."""
        if self._owns_client:
            await self._client.aclose()

    def _chat_payload(self, request: ProviderChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model.name,
            "messages": messages_to_openai(request.messages),
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop"] = list(request.stop)
        if request.tools:
            payload["tools"] = tools_to_openai(request.tools)
            payload["tool_choice"] = request.tool_choice
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if request.seed is not None:
            payload["seed"] = request.seed
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _headers(self, context: ProviderCallContext) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Request-ID": str(context.request_id),
        }
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        headers.update(context.extra_headers)
        return headers

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


__all__ = ["OpenAIProvider"]
