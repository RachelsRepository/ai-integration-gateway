"""Agent tool ports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ai_gateway.domain.entities.agent import ToolDefinition
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Context handed to a tool at invocation time.

    Attributes:
        tenant_id: Tenant on whose behalf the tool runs.
        request_id: Correlating request identifier.
        agent_run_id: Run that requested the invocation.
        deadline_seconds: Remaining budget for the invocation.
        attributes: Non-sensitive contextual values made available to the tool.
    """

    tenant_id: TenantId
    request_id: RequestId
    agent_run_id: str
    deadline_seconds: float = 15.0
    attributes: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Tool(Protocol):
    """An executable capability exposed to agents."""

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool's public contract."""
        ...

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        """Run the tool.

        Args:
            arguments: Validated arguments matching the declared JSON Schema.
            context: Invocation context.

        Returns:
            A serialised result appended to the model transcript.
        """
        ...


@runtime_checkable
class ToolRegistry(Protocol):
    """Resolves tool names to executable tools."""

    def get(self, name: str) -> Tool:
        """Fetch a registered tool.

        Args:
            name: Tool name.

        Returns:
            The tool.
        """
        ...

    def definitions(self, names: Sequence[str] | None = None) -> tuple[ToolDefinition, ...]:
        """Return the contracts of registered tools.

        Args:
            names: Restrict the result to these tool names; all tools when omitted.

        Returns:
            The matching tool contracts.
        """
        ...

    def names(self) -> tuple[str, ...]:
        """Return every registered tool name."""
        ...


__all__ = ["Tool", "ToolExecutionContext", "ToolRegistry"]
