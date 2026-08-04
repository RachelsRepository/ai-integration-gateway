"""Application-layer data transfer objects.

DTOs are the vocabulary shared between delivery adapters (HTTP, workers) and use cases.
They are plain dataclasses so the application layer stays framework free.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ai_gateway.domain.entities.agent import AgentRunStatus, AgentStep
from ai_gateway.domain.entities.message import FinishReason, Message, ToolCall
from ai_gateway.domain.entities.tenant import Principal, Tenant
from ai_gateway.domain.policies.routing import RoutingStrategy
from ai_gateway.domain.value_objects.identifiers import (
    AgentRunId,
    ConversationId,
    RequestId,
    TenantId,
    new_id,
)
from ai_gateway.domain.value_objects.model import ModelCapability, ModelRef
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.domain.value_objects.tokens import TokenUsage


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Cross-cutting context for a single inbound request.

    Attributes:
        principal: Authenticated caller.
        tenant: Resolved tenant configuration.
        request_id: Correlating request identifier.
        received_at: Arrival timestamp in UTC.
        source_ip: Client address, when available.
        user_agent: Client user agent, when available.
        trace_id: Distributed trace identifier.
        idempotency_key: Caller-supplied idempotency key.
        deadline_seconds: Total budget for the request.
    """

    principal: Principal
    tenant: Tenant
    request_id: RequestId = field(default_factory=lambda: RequestId(new_id()))
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source_ip: str | None = None
    user_agent: str | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None
    deadline_seconds: float = 60.0
    provider_scenario: str | None = None

    @property
    def tenant_id(self) -> TenantId:
        """Return the tenant identifier the request acts under."""
        return self.principal.tenant_id


@dataclass(frozen=True, slots=True)
class ToolSpecDTO:
    """A tool offered to the model on a chat request.

    Attributes:
        name: Tool name.
        description: Natural-language description.
        parameters: JSON Schema for the arguments.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatCompletionCommand:
    """A request to generate a chat completion.

    Attributes:
        messages: Ordered transcript supplied by the caller.
        model: Explicit ``provider/model`` pin, when the caller requires one.
        prompt_name: Managed prompt to render instead of supplying raw messages.
        prompt_version: Managed prompt version; the active version when omitted.
        prompt_variables: Values substituted into the managed prompt.
        conversation_id: Conversation to load history from and append to.
        max_output_tokens: Completion budget.
        temperature: Sampling temperature.
        top_p: Nucleus sampling parameter.
        stop: Stop sequences.
        stream: Whether the caller requested a streamed response.
        tools: Tools offered to the model.
        tool_choice: Tool selection strategy.
        response_format: ``text`` or ``json_object``.
        routing_strategy: Objective used to rank routing candidates.
        allow_fallback: Whether the gateway may fail over to another provider.
        cache: Whether the response cache may be read and written.
        seed: Deterministic sampling seed.
        metadata: Caller annotations echoed into usage and audit records.
    """

    messages: tuple[Message, ...] = ()
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: int | None = None
    prompt_variables: dict[str, Any] = field(default_factory=dict)
    conversation_id: ConversationId | None = None
    max_output_tokens: int = 512
    temperature: float = 0.7
    top_p: float | None = None
    stop: tuple[str, ...] = ()
    stream: bool = False
    tools: tuple[ToolSpecDTO, ...] = ()
    tool_choice: str = "auto"
    response_format: str = "text"
    routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    allow_fallback: bool = True
    cache: bool = True
    seed: int | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def required_capabilities(self) -> frozenset[ModelCapability]:
        """Return the capabilities the request implies."""
        required = {ModelCapability.CHAT}
        if self.stream:
            required.add(ModelCapability.STREAMING)
        if self.tools:
            required.add(ModelCapability.TOOL_CALLING)
        if self.response_format == "json_object":
            required.add(ModelCapability.JSON_MODE)
        return frozenset(required)


@dataclass(frozen=True, slots=True)
class RoutingTrace:
    """How a request was routed, surfaced to callers and traces.

    Attributes:
        selected_model: Model that ultimately served the request.
        strategy: Routing objective applied.
        reason: Human readable justification.
        attempts: Ordered list of models attempted.
        fallback_used: Whether a fallback candidate served the request.
        rejected: Models excluded during filtering, with reasons.
    """

    selected_model: str
    strategy: RoutingStrategy
    reason: str
    attempts: tuple[str, ...] = ()
    fallback_used: bool = False
    rejected: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatCompletionResult:
    """The outcome of a chat completion.

    Attributes:
        request_id: Correlating request identifier.
        model: Model that served the request.
        message: Assistant turn.
        usage: Token counters.
        cost: Billable cost.
        finish_reason: Normalised stop reason.
        latency_ms: End-to-end latency measured by the gateway.
        routing: Routing trace.
        cached: Whether the response came from the gateway cache.
        conversation_id: Conversation the turn was appended to.
        created_at: Completion timestamp.
    """

    request_id: RequestId
    model: ModelRef
    message: Message
    usage: TokenUsage
    cost: Money
    finish_reason: FinishReason
    latency_ms: int
    routing: RoutingTrace
    cached: bool = False
    conversation_id: ConversationId | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def content(self) -> str:
        """Return the assistant text content."""
        return self.message.content

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        """Return the tool calls requested by the model."""
        return self.message.tool_calls


class StreamEventType(StrEnum):
    """Kinds of event emitted on a streamed response."""

    START = "start"
    DELTA = "delta"
    TOOL_CALL = "tool_call"
    USAGE = "usage"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One event on a streamed response.

    Attributes:
        type: Event kind.
        data: JSON-serialisable payload.
        index: Monotonic event index within the stream.
    """

    type: StreamEventType
    data: dict[str, Any] = field(default_factory=dict)
    index: int = 0


@dataclass(frozen=True, slots=True)
class EmbeddingsCommand:
    """A request to embed one or more texts.

    Attributes:
        inputs: Texts to embed.
        model: Explicit ``provider/model`` pin.
        dimensions: Requested vector width.
        cache: Whether the embedding cache may be used.
        routing_strategy: Objective used to rank routing candidates.
        metadata: Caller annotations.
    """

    inputs: tuple[str, ...]
    model: str | None = None
    dimensions: int | None = None
    cache: bool = True
    routing_strategy: RoutingStrategy = RoutingStrategy.COST_OPTIMIZED
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmbeddingsResult:
    """The outcome of an embeddings request.

    Attributes:
        request_id: Correlating request identifier.
        model: Model that produced the vectors.
        vectors: One vector per input, in input order.
        usage: Token counters.
        cost: Billable cost.
        latency_ms: End-to-end latency.
        cache_hits: Number of inputs served from cache.
    """

    request_id: RequestId
    model: ModelRef
    vectors: tuple[tuple[float, ...], ...]
    usage: TokenUsage
    cost: Money
    latency_ms: int
    cache_hits: int = 0

    @property
    def dimensions(self) -> int:
        """Return the width of the returned vectors."""
        return len(self.vectors[0]) if self.vectors else 0


@dataclass(frozen=True, slots=True)
class AgentRunCommand:
    """A request to execute an agent.

    Attributes:
        input: The user instruction that starts the run.
        agent_name: Human readable agent name.
        instructions: System instructions for the run.
        tools: Tool names the agent may call.
        model: Explicit ``provider/model`` pin.
        max_iterations: Model/tool cycle budget.
        conversation_id: Conversation providing memory and receiving new turns.
        temperature: Sampling temperature.
        max_output_tokens: Completion budget per model call.
        stream: Whether the caller requested streamed step events.
        metadata: Caller annotations.
    """

    input: str
    agent_name: str = "default"
    instructions: str = "You are a helpful assistant operating inside a secure gateway."
    tools: tuple[str, ...] = ()
    model: str | None = None
    max_iterations: int = 6
    conversation_id: ConversationId | None = None
    temperature: float = 0.2
    max_output_tokens: int = 1024
    stream: bool = False
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """The outcome of an agent run.

    Attributes:
        run_id: Run identifier.
        request_id: Correlating request identifier.
        status: Terminal run status.
        output: Final assistant answer.
        steps: Recorded steps.
        usage: Aggregate token usage.
        cost: Aggregate cost.
        latency_ms: Wall-clock duration.
        conversation_id: Conversation the run was attached to.
        error: Failure description when the run failed.
    """

    run_id: AgentRunId
    request_id: RequestId
    status: AgentRunStatus
    output: str | None
    steps: tuple[AgentStep, ...]
    usage: TokenUsage
    cost: Money
    latency_ms: int
    conversation_id: ConversationId | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PromptPublishCommand:
    """A request to create or update a managed prompt.

    Attributes:
        name: Tenant-scoped prompt name.
        template: Template body with ``{{ variable }}`` placeholders.
        description: Human readable description.
        system_prompt: Optional system instruction.
        safety_prompt: Optional guardrail instruction.
        required_variables: Declared variables; inferred from the body when omitted.
        activate: Whether the new revision becomes active.
        notes: Change description recorded for audit.
        labels: Classification labels.
    """

    name: str
    template: str
    description: str | None = None
    system_prompt: str | None = None
    safety_prompt: str | None = None
    required_variables: frozenset[str] | None = None
    activate: bool = True
    notes: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PromptView:
    """A read model of a managed prompt.

    Attributes:
        id: Prompt identifier.
        name: Prompt name.
        description: Human readable description.
        active_version: Currently active revision.
        versions: Published revisions, newest first.
        labels: Classification labels.
        updated_at: Timestamp of the most recent publication.
    """

    id: str
    name: str
    description: str | None
    active_version: int | None
    versions: tuple[PromptVersionView, ...]
    labels: dict[str, str] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class PromptVersionView:
    """A read model of one prompt revision.

    Attributes:
        version: Revision number.
        template: Template body.
        system_prompt: System instruction.
        safety_prompt: Guardrail instruction.
        required_variables: Declared variables.
        created_at: Publication timestamp.
        created_by: Publishing principal.
        notes: Change description.
    """

    version: int
    template: str
    system_prompt: str | None
    safety_prompt: str | None
    required_variables: tuple[str, ...]
    created_at: datetime
    created_by: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ModelView:
    """A read model describing a routable model.

    Attributes:
        id: Qualified ``provider/model`` reference.
        provider: Hosting provider.
        capabilities: Advertised capabilities.
        context_window: Maximum combined tokens.
        max_output_tokens: Maximum completion tokens.
        input_cost_per_1k: Prompt token price.
        output_cost_per_1k: Completion token price.
        tier: Cost/quality tier.
        expected_latency_ms: Published latency expectation.
        available: Whether the model is currently routable for the caller.
        deprecated: Whether the model is being retired.
    """

    id: str
    provider: ProviderName
    capabilities: tuple[ModelCapability, ...]
    context_window: int
    max_output_tokens: int
    input_cost_per_1k: Money
    output_cost_per_1k: Money
    tier: str
    expected_latency_ms: int
    available: bool = True
    deprecated: bool = False


@dataclass(frozen=True, slots=True)
class ProviderView:
    """A read model describing a configured provider.

    Attributes:
        name: Provider identifier.
        status: Last observed health.
        circuit_state: Circuit breaker state.
        models: Qualified references hosted by the provider.
        observed_latency_ms: Rolling latency observation.
        error_rate: Rolling error rate.
        enabled_for_tenant: Whether tenant policy permits this provider.
    """

    name: ProviderName
    status: ProviderStatus
    circuit_state: str
    models: tuple[str, ...]
    observed_latency_ms: int | None = None
    error_rate: float = 0.0
    enabled_for_tenant: bool = True


@dataclass(frozen=True, slots=True)
class UsageReport:
    """Aggregated consumption for a tenant.

    Attributes:
        tenant_id: Tenant reported on.
        period_start: Inclusive start of the reporting window.
        period_end: Inclusive end of the reporting window.
        requests: Total requests.
        usage: Accumulated token counters.
        cost: Accumulated spend.
        by_model: Spend keyed by qualified model reference.
    """

    tenant_id: TenantId
    period_start: datetime
    period_end: datetime
    requests: int
    usage: TokenUsage
    cost: Money
    by_model: dict[str, Money] = field(default_factory=dict)


__all__ = [
    "AgentRunCommand",
    "AgentRunResult",
    "ChatCompletionCommand",
    "ChatCompletionResult",
    "EmbeddingsCommand",
    "EmbeddingsResult",
    "ModelView",
    "PromptPublishCommand",
    "PromptVersionView",
    "PromptView",
    "ProviderView",
    "RequestContext",
    "RoutingTrace",
    "StreamEvent",
    "StreamEventType",
    "ToolSpecDTO",
    "UsageReport",
]
