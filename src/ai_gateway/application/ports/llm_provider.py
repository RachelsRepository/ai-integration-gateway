"""The provider port: the single contract every upstream vendor must satisfy.

This is the seam that makes providers swappable. Business logic depends only on the
structures defined here; wire formats, authentication schemes and vendor quirks live
entirely inside the adapters.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ai_gateway.domain.entities.message import FinishReason, Message, ToolCall
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.domain.value_objects.model import ModelRef, ModelSpec
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.domain.value_objects.tokens import TokenUsage


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """A tool exposed to the model in provider-neutral form.

    Attributes:
        name: Tool name.
        description: Natural-language description.
        parameters: JSON Schema for the tool arguments.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderCallContext:
    """Cross-cutting context propagated into every provider call.

    Attributes:
        request_id: Correlating request identifier.
        tenant_id: Tenant on whose behalf the call is made.
        timeout_seconds: Hard deadline for the call.
        attempt: One-based attempt counter for retried calls.
        idempotency_key: Key forwarded to providers that support idempotent retries.
        trace_id: Distributed trace identifier for upstream correlation.
    """

    request_id: RequestId
    tenant_id: TenantId
    timeout_seconds: float = 60.0
    attempt: int = 1
    idempotency_key: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderChatRequest:
    """A normalised chat completion request.

    Attributes:
        model: Target model reference.
        messages: Ordered transcript.
        max_output_tokens: Completion budget.
        temperature: Sampling temperature.
        top_p: Nucleus sampling parameter.
        stop: Stop sequences.
        tools: Tools offered to the model.
        tool_choice: Tool selection strategy: ``auto``, ``none``, ``required`` or a name.
        response_format: ``text`` or ``json_object``.
        seed: Deterministic sampling seed where supported.
        metadata: Provider-neutral annotations forwarded when supported.
    """

    model: ModelRef
    messages: tuple[Message, ...]
    max_output_tokens: int = 512
    temperature: float = 0.7
    top_p: float | None = None
    stop: tuple[str, ...] = ()
    tools: tuple[ToolSchema, ...] = ()
    tool_choice: str = "auto"
    response_format: str = "text"
    seed: int | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderChatResponse:
    """A normalised chat completion response.

    Attributes:
        model: Model that produced the response.
        message: Assistant turn returned by the provider.
        usage: Token counters reported by the provider.
        finish_reason: Normalised stop reason.
        provider_request_id: Upstream request identifier, when exposed.
        raw_finish_reason: Vendor-specific stop reason, preserved for diagnostics.
        latency_ms: Round-trip latency measured by the adapter.
    """

    model: ModelRef
    message: Message
    usage: TokenUsage
    finish_reason: FinishReason = FinishReason.STOP
    provider_request_id: str | None = None
    raw_finish_reason: str | None = None
    latency_ms: int = 0

    @property
    def content(self) -> str:
        """Return the assistant text content."""
        return self.message.content

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        """Return the tool calls requested by the model."""
        return self.message.tool_calls


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One incremental event in a streamed completion.

    Attributes:
        delta: Text appended by this chunk.
        tool_call_delta: Partial tool-call payload, when the model is emitting a call.
        finish_reason: Present only on the terminal chunk.
        usage: Present only on the terminal chunk when the provider reports it.
        index: Zero-based chunk index.
    """

    delta: str = ""
    tool_call_delta: dict[str, Any] | None = None
    finish_reason: FinishReason | None = None
    usage: TokenUsage | None = None
    index: int = 0

    @property
    def is_final(self) -> bool:
        """Return ``True`` when this chunk terminates the stream."""
        return self.finish_reason is not None


@dataclass(frozen=True, slots=True)
class EmbeddingsRequest:
    """A normalised embeddings request.

    Attributes:
        model: Target embedding model.
        inputs: Texts to embed.
        dimensions: Requested vector width, where the provider supports truncation.
        encoding_format: ``float`` or ``base64``.
    """

    model: ModelRef
    inputs: tuple[str, ...]
    dimensions: int | None = None
    encoding_format: str = "float"


@dataclass(frozen=True, slots=True)
class EmbeddingsResponse:
    """A normalised embeddings response.

    Attributes:
        model: Model that produced the vectors.
        vectors: One vector per input, in input order.
        usage: Token counters reported by the provider.
        latency_ms: Round-trip latency measured by the adapter.
    """

    model: ModelRef
    vectors: tuple[tuple[float, ...], ...]
    usage: TokenUsage
    latency_ms: int = 0

    @property
    def dimensions(self) -> int:
        """Return the width of the returned vectors."""
        return len(self.vectors[0]) if self.vectors else 0


@runtime_checkable
class LLMProvider(Protocol):
    """The contract every provider adapter implements.

    Adapters translate between this contract and a vendor API. They must:

    * map vendor errors onto the gateway's :mod:`ai_gateway.domain.errors` hierarchy;
    * honour ``context.timeout_seconds`` and cooperative cancellation;
    * never leak vendor SDK types across the boundary.
    """

    @property
    def name(self) -> ProviderName:
        """Return the provider identifier."""
        ...

    def supported_models(self) -> tuple[ModelSpec, ...]:
        """Return the catalogue entries this adapter can serve."""
        ...

    async def chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> ProviderChatResponse:
        """Execute a chat completion.

        Args:
            request: Normalised request.
            context: Cross-cutting call context.

        Returns:
            The normalised response.
        """
        ...

    def stream_chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> AsyncIterator[StreamChunk]:
        """Execute a streaming chat completion.

        Args:
            request: Normalised request.
            context: Cross-cutting call context.

        Returns:
            An async iterator of incremental chunks.
        """
        ...

    async def embed(
        self, request: EmbeddingsRequest, context: ProviderCallContext
    ) -> EmbeddingsResponse:
        """Compute embeddings.

        Args:
            request: Normalised request.
            context: Cross-cutting call context.

        Returns:
            The normalised response.
        """
        ...

    async def health_check(self) -> ProviderStatus:
        """Probe upstream availability.

        Returns:
            The observed provider status.
        """
        ...

    async def aclose(self) -> None:
        """Release any adapter-held resources such as connection pools."""
        ...


__all__ = [
    "EmbeddingsRequest",
    "EmbeddingsResponse",
    "LLMProvider",
    "ProviderCallContext",
    "ProviderChatRequest",
    "ProviderChatResponse",
    "StreamChunk",
    "ToolSchema",
]
