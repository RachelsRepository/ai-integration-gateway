"""Azure OpenAI provider adapter."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
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
from ai_gateway.domain.errors import ValidationError
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


class AzureOpenAIProvider:
    """Adapter for Azure OpenAI deployments."""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        api_version: str = "2024-06-01",
        deployments: Mapping[str, str] | None = None,
        catalog: StaticModelCatalog | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise the adapter."""
        if not endpoint:
            raise ValidationError("Azure OpenAI endpoint is required")
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/")
        self._api_version = api_version
        self._deployments = dict(deployments or {})
        catalog = catalog or StaticModelCatalog()
        self._models = tuple(catalog.for_provider(ProviderName.AZURE_OPENAI))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=60.0)

    @property
    def name(self) -> ProviderName:
        """Return the provider identifier."""
        return ProviderName.AZURE_OPENAI

    def supported_models(self) -> tuple[ModelSpec, ...]:
        """Return supported models."""
        return self._models

    async def chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> ProviderChatResponse:
        """Execute a chat completion against a deployment."""
        started = time.perf_counter()
        payload = self._chat_payload(request, stream=False)
        data = await self._post(self._url(request.model.name, "chat/completions"), payload, context)
        choice = (data.get("choices") or [{}])[0]
        message_raw = choice.get("message") or {}
        usage_raw = data.get("usage") or {}
        return ProviderChatResponse(
            model=request.model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content=str(message_raw.get("content") or ""),
                tool_calls=parse_openai_tool_calls(message_raw.get("tool_calls")),
            ),
            usage=TokenUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            ),
            finish_reason=parse_finish_reason(choice.get("finish_reason")),
            provider_request_id=data.get("id"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream_chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion against a deployment."""
        payload = self._chat_payload(request, stream=True)
        index = 0
        try:
            async with self._client.stream(
                "POST",
                self._url(request.model.name, "chat/completions"),
                json=payload,
                headers=self._headers(context),
                timeout=context.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise map_http_error(self.name, response)
                async for data in iter_sse_lines(response):
                    chunk = json.loads(data)
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    finish = choice.get("finish_reason")
                    usage_raw = chunk.get("usage")
                    yield StreamChunk(
                        delta=str(delta.get("content") or ""),
                        finish_reason=parse_finish_reason(finish) if finish else None,
                        usage=(
                            TokenUsage(
                                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
                            )
                            if usage_raw
                            else None
                        ),
                        index=index,
                    )
                    index += 1
        except httpx.HTTPError as exc:
            raise map_transport_error(self.name, exc) from exc

    async def embed(
        self, request: EmbeddingsRequest, context: ProviderCallContext
    ) -> EmbeddingsResponse:
        """Compute embeddings against a deployment."""
        started = time.perf_counter()
        payload: dict[str, Any] = {"input": list(request.inputs)}
        if request.dimensions is not None:
            payload["dimensions"] = request.dimensions
        data = await self._post(self._url(request.model.name, "embeddings"), payload, context)
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
        """Probe the Azure endpoint."""
        try:
            response = await self._client.get(
                f"{self._endpoint}/openai/models?api-version={self._api_version}",
                headers={"api-key": self._api_key},
                timeout=5.0,
            )
            return ProviderStatus.HEALTHY if response.status_code < 500 else ProviderStatus.DEGRADED
        except httpx.HTTPError:
            return ProviderStatus.UNAVAILABLE

    async def aclose(self) -> None:
        """Close owned resources."""
        if self._owns_client:
            await self._client.aclose()

    def _url(self, model_name: str, suffix: str) -> str:
        deployment = self._deployments.get(model_name, model_name)
        return (
            f"{self._endpoint}/openai/deployments/{deployment}/{suffix}"
            f"?api-version={self._api_version}"
        )

    def _chat_payload(self, request: ProviderChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": messages_to_openai(request.messages),
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.tools:
            payload["tools"] = tools_to_openai(request.tools)
            payload["tool_choice"] = request.tool_choice
        return payload

    def _headers(self, context: ProviderCallContext) -> dict[str, str]:
        return {
            "api-key": self._api_key,
            "Content-Type": "application/json",
            "X-Request-ID": str(context.request_id),
        }

    async def _post(
        self, url: str, payload: dict[str, Any], context: ProviderCallContext
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                url,
                json=payload,
                headers=self._headers(context),
                timeout=context.timeout_seconds,
            )
            if response.status_code >= 400:
                raise map_http_error(self.name, response)
            return dict(require_mapping(response.json(), self.name))
        except httpx.HTTPError as exc:
            raise map_transport_error(self.name, exc) from exc


__all__ = ["AzureOpenAIProvider"]
