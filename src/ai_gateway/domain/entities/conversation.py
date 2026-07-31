"""Conversation aggregate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ai_gateway.domain.entities.message import Message, MessageRole
from ai_gateway.domain.errors import ConflictError, ValidationError
from ai_gateway.domain.value_objects.identifiers import (
    ConversationId,
    TenantId,
    UserId,
    new_id,
)
from ai_gateway.domain.value_objects.tokens import TokenUsage


class ConversationStatus(StrEnum):
    """Lifecycle state of a conversation."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(slots=True)
class Conversation:
    """An ordered, tenant-scoped sequence of messages.

    The aggregate owns the invariants around message ordering, tenant ownership and
    context-window trimming. It is intentionally mutable: it is the transactional
    consistency boundary for conversation state.

    Attributes:
        tenant_id: Owning tenant.
        id: Stable conversation identifier.
        user_id: Optional end-user the conversation belongs to.
        title: Human readable label.
        status: Lifecycle state.
        messages: Ordered turns.
        cumulative_usage: Aggregate token usage across all turns.
        created_at: Creation timestamp in UTC.
        updated_at: Timestamp of the most recent mutation.
        version: Optimistic-concurrency version counter.
        metadata: Free-form annotations.
    """

    tenant_id: TenantId
    id: ConversationId = field(default_factory=lambda: ConversationId(new_id()))
    user_id: UserId | None = None
    title: str | None = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    messages: list[Message] = field(default_factory=list)
    cumulative_usage: TokenUsage = field(default_factory=TokenUsage.empty)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 0
    metadata: dict[str, str] = field(default_factory=dict)

    def append(self, message: Message, *, now: datetime | None = None) -> None:
        """Append a message to the conversation.

        Args:
            message: The turn to append.
            now: Injected clock value; defaults to the current UTC time.

        Raises:
            ConflictError: If the conversation is not active.
        """
        if self.status is not ConversationStatus.ACTIVE:
            raise ConflictError(
                "Cannot append to a conversation that is not active",
                details={"conversation_id": self.id, "status": self.status.value},
            )
        self.messages.append(message)
        self._touch(now)

    def extend(self, messages: list[Message], *, now: datetime | None = None) -> None:
        """Append several messages in order.

        Args:
            messages: Turns to append.
            now: Injected clock value.
        """
        for message in messages:
            self.append(message, now=now)

    def record_usage(self, usage: TokenUsage, *, now: datetime | None = None) -> None:
        """Accumulate token usage against the conversation.

        Args:
            usage: Usage reported for the latest turn.
            now: Injected clock value.
        """
        self.cumulative_usage = self.cumulative_usage + usage
        self._touch(now)

    def archive(self, *, now: datetime | None = None) -> None:
        """Archive the conversation, preventing further turns.

        Args:
            now: Injected clock value.
        """
        self.status = ConversationStatus.ARCHIVED
        self._touch(now)

    def soft_delete(self, *, now: datetime | None = None) -> None:
        """Mark the conversation as deleted without destroying history.

        Args:
            now: Injected clock value.
        """
        self.status = ConversationStatus.DELETED
        self._touch(now)

    def assert_owned_by(self, tenant_id: TenantId) -> None:
        """Verify tenant ownership.

        Args:
            tenant_id: Tenant asserted by the caller's credentials.

        Raises:
            ValidationError: If the conversation belongs to a different tenant.
        """
        if self.tenant_id != tenant_id:
            raise ValidationError(
                "Conversation does not belong to the requesting tenant",
                details={"conversation_id": self.id},
            )

    @property
    def system_prompt(self) -> str | None:
        """Return the first system message content, if any."""
        for message in self.messages:
            if message.role is MessageRole.SYSTEM:
                return message.content
        return None

    def approximate_tokens(self) -> int:
        """Estimate the token footprint of the full transcript.

        Returns:
            The estimated token count.
        """
        return sum(message.approximate_tokens() for message in self.messages)

    def history(self, *, limit: int | None = None) -> list[Message]:
        """Return the transcript, optionally limited to the most recent turns.

        The leading system message is always preserved so that instructions survive
        truncation.

        Args:
            limit: Maximum number of non-system turns to return.

        Returns:
            The selected transcript slice.
        """
        if limit is None or len(self.messages) <= limit:
            return list(self.messages)
        head = [m for m in self.messages[:1] if m.role is MessageRole.SYSTEM]
        tail = self.messages[-limit:]
        return head + [m for m in tail if m not in head]

    def trim_to_token_budget(self, budget_tokens: int) -> list[Message]:
        """Drop the oldest turns until the transcript fits a token budget.

        Args:
            budget_tokens: Maximum estimated tokens allowed.

        Returns:
            The messages that were removed, oldest first.

        Raises:
            ValidationError: If the budget is not positive.
        """
        if budget_tokens <= 0:
            raise ValidationError("Token budget must be positive")
        removed: list[Message] = []
        while self.messages and self.approximate_tokens() > budget_tokens:
            index = 1 if self.messages[0].role is MessageRole.SYSTEM else 0
            if index >= len(self.messages):
                break
            removed.append(self.messages.pop(index))
        return removed

    def is_stale(self, *, retention: timedelta, now: datetime) -> bool:
        """Report whether the conversation is eligible for cleanup.

        Args:
            retention: Maximum idle period before cleanup.
            now: Current time.

        Returns:
            ``True`` when the conversation has been idle for longer than ``retention``.
        """
        return now - self.updated_at > retention

    def _touch(self, now: datetime | None) -> None:
        self.updated_at = now or datetime.now(UTC)
        self.version += 1


__all__ = ["Conversation", "ConversationStatus"]
