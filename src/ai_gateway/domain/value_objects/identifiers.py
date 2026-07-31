"""Strongly typed identifiers.

Distinct ``NewType`` aliases prevent accidentally passing a conversation identifier where
a tenant identifier is expected. They are erased at runtime and therefore free.
"""

from __future__ import annotations

import uuid
from typing import NewType

TenantId = NewType("TenantId", str)
UserId = NewType("UserId", str)
ApiKeyId = NewType("ApiKeyId", str)
RequestId = NewType("RequestId", str)
ConversationId = NewType("ConversationId", str)
MessageId = NewType("MessageId", str)
PromptId = NewType("PromptId", str)
AgentRunId = NewType("AgentRunId", str)


def new_id() -> str:
    """Generate a new opaque, collision-resistant identifier.

    Returns:
        A canonical UUID4 string.
    """
    return str(uuid.uuid4())


__all__ = [
    "AgentRunId",
    "ApiKeyId",
    "ConversationId",
    "MessageId",
    "PromptId",
    "RequestId",
    "TenantId",
    "UserId",
    "new_id",
]
