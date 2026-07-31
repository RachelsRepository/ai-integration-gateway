"""In-memory tool registry."""

from __future__ import annotations

from collections.abc import Sequence

from ai_gateway.application.ports.tools import Tool
from ai_gateway.domain.entities.agent import ToolDefinition
from ai_gateway.domain.errors import ToolNotFoundError


class InMemoryToolRegistry:
    """Resolves tool names to executable tools held in process memory."""

    def __init__(self, tools: Sequence[Tool] | None = None) -> None:
        """Initialise the registry.

        Args:
            tools: Tools to register.
        """
        self._tools: dict[str, Tool] = {}
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Register a tool.

        Args:
            tool: Tool to register.
        """
        self._tools[tool.definition.name] = tool

    def get(self, name: str) -> Tool:
        """Fetch a registered tool.

        Args:
            name: Tool name.

        Returns:
            The tool.

        Raises:
            ToolNotFoundError: If the tool is not registered.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError("Tool is not registered", details={"tool": name})
        return tool

    def definitions(self, names: Sequence[str] | None = None) -> tuple[ToolDefinition, ...]:
        """Return tool contracts.

        Args:
            names: Restrict to these names; all tools when omitted.

        Returns:
            The matching contracts.
        """
        if names is None:
            return tuple(tool.definition for tool in self._tools.values())
        return tuple(self.get(name).definition for name in names if name in self._tools)

    def names(self) -> tuple[str, ...]:
        """Return every registered tool name."""
        return tuple(sorted(self._tools))


__all__ = ["InMemoryToolRegistry"]
