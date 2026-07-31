"""Domain entities and aggregates."""

from __future__ import annotations

from ai_gateway.domain.entities.agent import (
    AgentDefinition,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepType,
    ToolDefinition,
    ToolInvocation,
)
from ai_gateway.domain.entities.audit import AuditEvent, AuditOutcome
from ai_gateway.domain.entities.conversation import Conversation, ConversationStatus
from ai_gateway.domain.entities.message import (
    FinishReason,
    Message,
    MessageRole,
    ToolCall,
)
from ai_gateway.domain.entities.prompt import (
    PromptTemplate,
    PromptVersion,
    RenderedPrompt,
)
from ai_gateway.domain.entities.tenant import (
    ApiKey,
    Permission,
    Principal,
    Quota,
    QuotaPeriod,
    Role,
    RoutingPreferences,
    Tenant,
    TenantStatus,
)
from ai_gateway.domain.entities.usage import OperationType, UsageAggregate, UsageRecord

__all__ = [
    "AgentDefinition",
    "AgentRun",
    "AgentRunStatus",
    "AgentStep",
    "AgentStepType",
    "ApiKey",
    "AuditEvent",
    "AuditOutcome",
    "Conversation",
    "ConversationStatus",
    "FinishReason",
    "Message",
    "MessageRole",
    "OperationType",
    "Permission",
    "Principal",
    "PromptTemplate",
    "PromptVersion",
    "Quota",
    "QuotaPeriod",
    "RenderedPrompt",
    "Role",
    "RoutingPreferences",
    "Tenant",
    "TenantStatus",
    "ToolCall",
    "ToolDefinition",
    "ToolInvocation",
    "UsageAggregate",
    "UsageRecord",
]
