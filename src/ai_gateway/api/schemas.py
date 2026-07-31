"""Pydantic request/response schemas for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    """Base schema with ORM-friendly configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ChatMessageSchema(APIModel):
    """A chat message supplied by the caller."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class ToolSpecSchema(APIModel):
    """A tool offered to the model."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ChatCompletionRequest(APIModel):
    """``POST /v1/chat/completions`` body."""

    messages: list[ChatMessageSchema] = Field(default_factory=list)
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: int | None = None
    prompt_variables: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = None
    max_tokens: int = Field(default=512, ge=1, le=128_000, alias="max_output_tokens")
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    stop: list[str] = Field(default_factory=list)
    stream: bool = False
    tools: list[ToolSpecSchema] = Field(default_factory=list)
    tool_choice: str = "auto"
    response_format: Literal["text", "json_object"] = "text"
    routing_strategy: Literal[
        "cost_optimized",
        "latency_optimized",
        "quality_optimized",
        "balanced",
        "failover",
    ] = "balanced"
    allow_fallback: bool = True
    cache: bool = True
    seed: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class UsageSchema(APIModel):
    """Token usage counters."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0


class ChatCompletionResponse(APIModel):
    """Non-streaming chat completion response."""

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[dict[str, Any]]
    usage: UsageSchema
    cost_micros: int = 0
    currency: str = "USD"
    latency_ms: int = 0
    cached: bool = False
    conversation_id: str | None = None
    routing: dict[str, Any] = Field(default_factory=dict)


class EmbeddingsRequest(APIModel):
    """``POST /v1/embeddings`` body."""

    input: str | list[str]
    model: str | None = None
    dimensions: int | None = Field(default=None, ge=1)
    cache: bool = True
    routing_strategy: Literal[
        "cost_optimized", "latency_optimized", "quality_optimized", "balanced", "failover"
    ] = "cost_optimized"
    metadata: dict[str, str] = Field(default_factory=dict)


class EmbeddingsResponse(APIModel):
    """Embeddings response."""

    object: Literal["list"] = "list"
    model: str
    data: list[dict[str, Any]]
    usage: UsageSchema
    cost_micros: int = 0
    currency: str = "USD"
    latency_ms: int = 0
    cache_hits: int = 0


class AgentRunRequest(APIModel):
    """``POST /v1/agents/run`` body."""

    input: str = Field(min_length=1)
    agent_name: str = "default"
    instructions: str = "You are a helpful assistant operating inside a secure gateway."
    tools: list[str] = Field(default_factory=list)
    model: str | None = None
    max_iterations: int = Field(default=6, ge=1, le=25)
    conversation_id: str | None = None
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=1024, ge=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentRunResponse(APIModel):
    """Agent run response."""

    run_id: str
    request_id: str
    status: str
    output: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    usage: UsageSchema
    cost_micros: int = 0
    currency: str = "USD"
    latency_ms: int = 0
    conversation_id: str | None = None
    error: str | None = None


class PromptPublishRequest(APIModel):
    """``POST /v1/prompts`` body."""

    name: str = Field(min_length=3, max_length=64)
    template: str = Field(min_length=1)
    description: str | None = None
    system_prompt: str | None = None
    safety_prompt: str | None = None
    required_variables: list[str] | None = None
    activate: bool = True
    notes: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class PromptResponse(APIModel):
    """Prompt read model."""

    id: str
    name: str
    description: str | None
    active_version: int | None
    versions: list[dict[str, Any]]
    labels: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime


class ModelResponse(APIModel):
    """Model catalogue entry."""

    id: str
    provider: str
    capabilities: list[str]
    context_window: int
    max_output_tokens: int
    input_cost_per_1k: str
    output_cost_per_1k: str
    tier: str
    expected_latency_ms: int
    available: bool = True
    deprecated: bool = False


class ProviderResponse(APIModel):
    """Provider catalogue entry."""

    name: str
    status: str
    circuit_state: str
    models: list[str]
    observed_latency_ms: int | None = None
    error_rate: float = 0.0
    enabled_for_tenant: bool = True


class ErrorResponse(APIModel):
    """Standard error envelope."""

    error: dict[str, Any]


class HealthResponse(APIModel):
    """Liveness/readiness response."""

    status: Literal["ok", "degraded", "unavailable"]
    service: str
    version: str
    checks: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "AgentRunRequest",
    "AgentRunResponse",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "EmbeddingsRequest",
    "EmbeddingsResponse",
    "ErrorResponse",
    "HealthResponse",
    "ModelResponse",
    "PromptPublishRequest",
    "PromptResponse",
    "ProviderResponse",
    "UsageSchema",
]
