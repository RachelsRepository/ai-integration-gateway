"""Dead-letter queue adapters."""

from __future__ import annotations

from ai_gateway.infrastructure.dlq.memory import InMemoryDeadLetterQueue

__all__ = ["InMemoryDeadLetterQueue"]
