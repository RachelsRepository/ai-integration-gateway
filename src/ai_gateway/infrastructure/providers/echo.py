"""Deterministic in-process provider used for tests and local development."""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
from collections.abc import AsyncIterator

from ai_gateway.application.ports.llm_provider import (
    EmbeddingsRequest,
    EmbeddingsResponse,
    ProviderCallContext,
    ProviderChatRequest,
    ProviderChatResponse,
    StreamChunk,
)
from ai_gateway.domain.entities.message import FinishReason, Message, MessageRole, ToolCall
from ai_gateway.domain.value_objects.model import ModelSpec
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog


class EchoProvider:
    """Echoes the last user message and produces deterministic embeddings."""

    def __init__(self, *, catalog: StaticModelCatalog | None = None) -> None:
        """Initialise the provider.

        Args:
            catalog: Model catalogue used to advertise supported models.
        """
        catalog = catalog or StaticModelCatalog()
        self._models = tuple(catalog.for_provider(ProviderName.ECHO))

    @property
    def name(self) -> ProviderName:
        """Return the provider identifier."""
        return ProviderName.ECHO

    def supported_models(self) -> tuple[ModelSpec, ...]:
        """Return the catalogue entries this adapter can serve."""
        return self._models

    async def chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> ProviderChatResponse:
        """Echo the last user message, or emit a tool call when tools are offered.

        Args:
            request: Normalised request.
            context: Call context.

        Returns:
            The echo response.
        """
        del context
        await asyncio.sleep(0)
        content, tool_calls, finish = self._compose(request)
        usage = TokenUsage(
            prompt_tokens=sum(m.approximate_tokens() for m in request.messages),
            completion_tokens=max(len(content) // 4, 1),
        )
        return ProviderChatResponse(
            model=request.model,
            message=Message(role=MessageRole.ASSISTANT, content=content, tool_calls=tool_calls),
            usage=usage,
            finish_reason=finish,
            latency_ms=1,
        )

    async def stream_chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> AsyncIterator[StreamChunk]:
        """Stream the echo response one word at a time.

        Args:
            request: Normalised request.
            context: Call context.

        Yields:
            Incremental chunks.
        """
        response = await self.chat(request, context)
        words = response.content.split(" ") if response.content else []
        for index, word in enumerate(words):
            delta = word if index == len(words) - 1 else f"{word} "
            yield StreamChunk(delta=delta, index=index)
            await asyncio.sleep(0)
        yield StreamChunk(
            finish_reason=response.finish_reason,
            usage=response.usage,
            index=len(words),
        )

    async def embed(
        self, request: EmbeddingsRequest, context: ProviderCallContext
    ) -> EmbeddingsResponse:
        """Return deterministic pseudo-embeddings derived from each input.

        Args:
            request: Normalised request.
            context: Call context.

        Returns:
            Deterministic vectors.
        """
        del context
        await asyncio.sleep(0)
        dimensions = request.dimensions or 8
        vectors = tuple(self._vector(text, dimensions) for text in request.inputs)
        usage = TokenUsage(prompt_tokens=sum(max(len(text) // 4, 1) for text in request.inputs))
        return EmbeddingsResponse(model=request.model, vectors=vectors, usage=usage, latency_ms=1)

    async def health_check(self) -> ProviderStatus:
        """Always report healthy."""
        return ProviderStatus.HEALTHY

    async def aclose(self) -> None:
        """No resources to release."""

    def _compose(
        self, request: ProviderChatRequest
    ) -> tuple[str, tuple[ToolCall, ...], FinishReason]:
        last_user = next(
            (m.content for m in reversed(request.messages) if m.role is MessageRole.USER),
            "",
        )
        has_tool_result = any(m.role is MessageRole.TOOL for m in request.messages)
        if request.tools and last_user.startswith("TOOL:") and not has_tool_result:
            _, _, rest = last_user.partition(":")
            tool_name, _, args = rest.partition(" ")
            name = tool_name.strip() or request.tools[0].name
            try:
                arguments = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                arguments = {"input": args.strip()}
            call = ToolCall(id="echo_tool_1", name=name, arguments=arguments)
            return "", (call,), FinishReason.TOOL_CALLS
        if has_tool_result:
            tool_output = next(
                (m.content for m in reversed(request.messages) if m.role is MessageRole.TOOL),
                "",
            )
            return f"echo: tool_result={tool_output}", (), FinishReason.STOP
        content = f"echo: {last_user}" if last_user else "echo: (empty)"
        if request.max_output_tokens > 0:
            content = content[: request.max_output_tokens * 4]
        return content, (), FinishReason.STOP

    @staticmethod
    def _vector(text: str, dimensions: int) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        while len(values) < dimensions:
            for offset in range(0, len(digest), 4):
                if len(values) >= dimensions:
                    break
                chunk = digest[offset : offset + 4]
                if len(chunk) < 4:
                    chunk = chunk.ljust(4, b"\0")
                value = struct.unpack("!I", chunk)[0] / 2**32
                values.append(value * 2 - 1)
            digest = hashlib.sha256(digest).digest()
        return tuple(values)


__all__ = ["EchoProvider"]
