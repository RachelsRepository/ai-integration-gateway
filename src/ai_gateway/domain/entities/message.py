"""Conversation message entities."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ai_gateway.domain.errors import ValidationError
from ai_gateway.domain.value_objects.identifiers import MessageId, new_id


class MessageRole(StrEnum):
    """Role of the author of a message."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    """Normalised reason a provider stopped generating."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model request to invoke a registered tool.

    Attributes:
        id: Provider-supplied correlation identifier for the call.
        name: Registered tool name.
        arguments: Parsed JSON arguments for the tool.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the tool call.

        Raises:
            ValidationError: If the tool name is empty.
        """
        if not self.name:
            raise ValidationError("Tool call must reference a tool name")


@dataclass(frozen=True, slots=True)
class Message:
    """A single turn in a conversation.

    Attributes:
        role: Author of the message.
        content: Text content; may be empty when the turn only carries tool calls.
        id: Stable message identifier.
        name: Optional author name, used for tool and multi-agent turns.
        tool_calls: Tool invocations requested by an assistant turn.
        tool_call_id: Correlation identifier when ``role`` is ``TOOL``.
        created_at: Creation timestamp in UTC.
        metadata: Free-form, non-authoritative annotations.
    """

    role: MessageRole
    content: str = ""
    id: MessageId = field(default_factory=lambda: MessageId(new_id()))
    name: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate role-specific invariants.

        Raises:
            ValidationError: If the message violates a role invariant.
        """
        if self.role is MessageRole.TOOL and not self.tool_call_id:
            raise ValidationError("Tool messages must carry a tool_call_id")
        if self.role is not MessageRole.ASSISTANT and self.tool_calls:
            raise ValidationError("Only assistant messages may request tool calls")
        if not self.content and not self.tool_calls and self.role is not MessageRole.ASSISTANT:
            raise ValidationError(f"{self.role.value} messages must not be empty")

    @classmethod
    def system(cls, content: str) -> Message:
        """Create a system message.

        Args:
            content: System instruction text.

        Returns:
            The created message.
        """
        return cls(role=MessageRole.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str, *, name: str | None = None) -> Message:
        """Create a user message.

        Args:
            content: User supplied text.
            name: Optional author name.

        Returns:
            The created message.
        """
        return cls(role=MessageRole.USER, content=content, name=name)

    @classmethod
    def assistant(cls, content: str = "", *, tool_calls: tuple[ToolCall, ...] = ()) -> Message:
        """Create an assistant message.

        Args:
            content: Assistant text output.
            tool_calls: Tool invocations requested by the model.

        Returns:
            The created message.
        """
        return cls(role=MessageRole.ASSISTANT, content=content, tool_calls=tool_calls)

    @classmethod
    def tool_result(cls, *, tool_call_id: str, name: str, content: str) -> Message:
        """Create a tool result message.

        Args:
            tool_call_id: Identifier of the originating tool call.
            name: Tool name.
            content: Serialised tool output.

        Returns:
            The created message.
        """
        return cls(
            role=MessageRole.TOOL,
            content=content,
            name=name,
            tool_call_id=tool_call_id,
        )

    def with_content(self, content: str) -> Message:
        """Return a copy of the message with replaced content.

        Args:
            content: Replacement content, typically redacted or filtered.

        Returns:
            A new message instance.
        """
        return replace(self, content=content)

    @property
    def has_tool_calls(self) -> bool:
        """Return ``True`` when the message requests tool execution."""
        return bool(self.tool_calls)

    def approximate_tokens(self) -> int:
        """Estimate the token footprint of the message.

        Uses the widely applied four-characters-per-token heuristic plus a fixed
        per-message overhead. Providers report authoritative counts; this estimate only
        drives pre-flight routing and context-window checks.

        Returns:
            The estimated token count.
        """
        payload = len(self.content)
        for call in self.tool_calls:
            payload += len(call.name) + len(str(call.arguments))
        return payload // 4 + 4


__all__ = ["FinishReason", "Message", "MessageRole", "ToolCall"]
