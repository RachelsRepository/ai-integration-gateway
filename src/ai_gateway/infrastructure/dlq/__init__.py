"""Dead-letter queue adapters."""

from __future__ import annotations

from ai_gateway.infrastructure.dlq.memory import InMemoryDeadLetterQueue
from ai_gateway.infrastructure.dlq.sqlalchemy import SqlDeadLetterQueue

__all__ = ["InMemoryDeadLetterQueue", "SqlDeadLetterQueue"]
