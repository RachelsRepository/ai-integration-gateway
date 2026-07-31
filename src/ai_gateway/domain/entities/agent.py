"""Agent orchestration entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ai_gateway.domain.entities.message import Message, ToolCall
from ai_gateway.domain.errors import MaxIterationsExceededError, ValidationError
from ai_gateway.domain.value_objects.identifiers import (
    AgentRunId,
    ConversationId,
    TenantId,
    new_id,
)
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.tokens import TokenUsage

_MAX_ITERATION_CEILING = 25


class AgentRunStatus(StrEnum):
    """Lifecycle state of an agent run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` when no further steps may be recorded."""
        return self in {
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
            AgentRunStatus.TIMED_OUT,
        }


class AgentStepType(StrEnum):
    """Kind of work performed in a single agent step."""

    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    FINAL_ANSWER = "final_answer"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """The contract of a tool exposed to an agent.

    Attributes:
        name: Unique tool name.
        description: Natural-language description used by the model.
        parameters_schema: JSON Schema describing the tool arguments.
        requires_confirmation: Whether a human approval gate applies.
        timeout_seconds: Per-invocation execution budget.
        tags: Classification labels used for allow-listing.
    """

    name: str
    description: str
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    timeout_seconds: float = 15.0
    tags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Validate the tool contract.

        Raises:
            ValidationError: If the name, description or timeout is invalid.
        """
        if not self.name.isidentifier():
            raise ValidationError(
                "Tool name must be a valid identifier", details={"name": self.name}
            )
        if not self.description.strip():
            raise ValidationError("Tool description must not be empty")
        if self.timeout_seconds <= 0:
            raise ValidationError("Tool timeout must be positive")


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """The outcome of executing a tool.

    Attributes:
        call: The originating tool call.
        output: Serialised tool output.
        succeeded: Whether execution completed without error.
        duration_ms: Wall-clock execution time.
        error: Error message when execution failed.
    """

    call: ToolCall
    output: str
    succeeded: bool = True
    duration_ms: int = 0
    error: str | None = None

    def to_message(self) -> Message:
        """Convert the invocation into a tool-role message.

        Returns:
            A message suitable for appending to the model transcript.
        """
        content = self.output if self.succeeded else f"ERROR: {self.error or 'tool failed'}"
        return Message.tool_result(tool_call_id=self.call.id, name=self.call.name, content=content)


@dataclass(frozen=True, slots=True)
class AgentStep:
    """A single recorded step in an agent run.

    Attributes:
        index: Zero-based step index.
        type: Kind of work performed.
        started_at: Step start timestamp in UTC.
        duration_ms: Wall-clock duration.
        usage: Token usage attributable to the step.
        cost: Cost attributable to the step.
        content: Assistant output produced by the step, if any.
        tool_invocations: Tool executions performed by the step.
        error: Error message when the step failed.
    """

    index: int
    type: AgentStepType
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage.empty)
    cost: Money = field(default_factory=Money.zero)
    content: str | None = None
    tool_invocations: tuple[ToolInvocation, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """A declarative agent configuration.

    Attributes:
        name: Agent name, unique per tenant.
        instructions: System instructions injected at the start of every run.
        tools: Names of tools the agent may invoke.
        max_iterations: Maximum model/tool cycles before the run is aborted.
        model: Optional pinned model reference in ``provider/model`` form.
        temperature: Sampling temperature applied to model calls.
        max_output_tokens: Completion budget per model call.
        tool_choice: Tool selection strategy passed to the provider.
        memory_window: Number of prior turns loaded from conversation memory.
    """

    name: str
    instructions: str
    tools: frozenset[str] = frozenset()
    max_iterations: int = 6
    model: str | None = None
    temperature: float = 0.2
    max_output_tokens: int = 1024
    tool_choice: str = "auto"
    memory_window: int = 20

    def __post_init__(self) -> None:
        """Validate the agent configuration.

        Raises:
            ValidationError: If the iteration budget is outside the allowed range.
        """
        if not 1 <= self.max_iterations <= _MAX_ITERATION_CEILING:
            raise ValidationError(
                "max_iterations must be between 1 and 25",
                details={"max_iterations": self.max_iterations},
            )
        if not self.instructions.strip():
            raise ValidationError("Agent instructions must not be empty")


@dataclass(slots=True)
class AgentRun:
    """A stateful execution of an agent definition.

    Attributes:
        tenant_id: Owning tenant.
        definition: Agent configuration being executed.
        id: Stable run identifier.
        conversation_id: Conversation the run is attached to.
        status: Lifecycle state.
        steps: Recorded steps, in execution order.
        output: Final assistant answer once the run succeeds.
        error: Failure description when the run fails.
        started_at: Run start timestamp in UTC.
        finished_at: Completion timestamp in UTC.
        total_usage: Aggregate token usage across steps.
        total_cost: Aggregate cost across steps.
        metadata: Free-form annotations.
    """

    tenant_id: TenantId
    definition: AgentDefinition
    id: AgentRunId = field(default_factory=lambda: AgentRunId(new_id()))
    conversation_id: ConversationId | None = None
    status: AgentRunStatus = AgentRunStatus.PENDING
    steps: list[AgentStep] = field(default_factory=list)
    output: str | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    total_usage: TokenUsage = field(default_factory=TokenUsage.empty)
    total_cost: Money = field(default_factory=Money.zero)
    metadata: dict[str, str] = field(default_factory=dict)

    def start(self) -> None:
        """Transition the run into the running state."""
        self.status = AgentRunStatus.RUNNING

    def record_step(self, step: AgentStep) -> None:
        """Append a step and fold its usage into the run totals.

        Args:
            step: The step to record.

        Raises:
            MaxIterationsExceededError: If the run has exhausted its step budget.
        """
        if len(self.steps) >= self.definition.max_iterations:
            raise MaxIterationsExceededError(
                "Agent exceeded its maximum iteration budget",
                details={
                    "run_id": self.id,
                    "max_iterations": self.definition.max_iterations,
                },
            )
        self.steps.append(step)
        self.total_usage = self.total_usage + step.usage
        self.total_cost = self.total_cost + step.cost

    @property
    def next_step_index(self) -> int:
        """Return the index the next recorded step will receive."""
        return len(self.steps)

    @property
    def iterations_remaining(self) -> int:
        """Return the number of steps still permitted."""
        return max(self.definition.max_iterations - len(self.steps), 0)

    def succeed(self, output: str, *, now: datetime | None = None) -> None:
        """Complete the run successfully.

        Args:
            output: Final assistant answer.
            now: Injected clock value.
        """
        self.output = output
        self.status = AgentRunStatus.SUCCEEDED
        self.finished_at = now or datetime.now(UTC)

    def fail(self, error: str, *, now: datetime | None = None) -> None:
        """Complete the run as failed.

        Args:
            error: Failure description.
            now: Injected clock value.
        """
        self.error = error
        self.status = AgentRunStatus.FAILED
        self.finished_at = now or datetime.now(UTC)

    def cancel(self, *, now: datetime | None = None) -> None:
        """Complete the run as cancelled.

        Args:
            now: Injected clock value.
        """
        self.status = AgentRunStatus.CANCELLED
        self.finished_at = now or datetime.now(UTC)

    def time_out(self, *, now: datetime | None = None) -> None:
        """Complete the run as timed out.

        Args:
            now: Injected clock value.
        """
        self.status = AgentRunStatus.TIMED_OUT
        self.finished_at = now or datetime.now(UTC)

    @property
    def duration_ms(self) -> int:
        """Return the wall-clock duration of the run in milliseconds."""
        end = self.finished_at or datetime.now(UTC)
        return int((end - self.started_at).total_seconds() * 1000)


__all__ = [
    "AgentDefinition",
    "AgentRun",
    "AgentRunStatus",
    "AgentStep",
    "AgentStepType",
    "ToolDefinition",
    "ToolInvocation",
]
