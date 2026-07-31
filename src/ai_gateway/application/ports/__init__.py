"""Ports: abstract interfaces the application requires from the outside world.

Every port is a :class:`typing.Protocol` so that adapters need no inheritance and test
doubles stay trivial to write. Dependency inversion is enforced statically by mypy and
architecturally by import-linter.
"""

from __future__ import annotations

from ai_gateway.application.ports.cache import Cache, DistributedLock, LockManager
from ai_gateway.application.ports.clock import Clock
from ai_gateway.application.ports.dlq import DeadLetterQueue, DeadLetterRecord
from ai_gateway.application.ports.events import EventPublisher
from ai_gateway.application.ports.health import ComponentHealth, HealthProbe
from ai_gateway.application.ports.llm_provider import (
    EmbeddingsRequest,
    EmbeddingsResponse,
    LLMProvider,
    ProviderCallContext,
    ProviderChatRequest,
    ProviderChatResponse,
    StreamChunk,
    ToolSchema,
)
from ai_gateway.application.ports.metrics import MetricsRecorder
from ai_gateway.application.ports.model_catalog import ModelCatalog
from ai_gateway.application.ports.rate_limiter import RateLimitDecision, RateLimiter
from ai_gateway.application.ports.repositories import (
    AgentRunRepository,
    ApiKeyRepository,
    AuditRepository,
    ConversationRepository,
    OutboxRepository,
    PromptRepository,
    TenantRepository,
    UnitOfWork,
    UsageRepository,
)
from ai_gateway.application.ports.resilience import CircuitBreaker, CircuitBreakerRegistry
from ai_gateway.application.ports.secrets import SecretResolver
from ai_gateway.application.ports.tools import Tool, ToolRegistry

__all__ = [
    "AgentRunRepository",
    "ApiKeyRepository",
    "AuditRepository",
    "Cache",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "Clock",
    "ComponentHealth",
    "ConversationRepository",
    "DeadLetterQueue",
    "DeadLetterRecord",
    "DistributedLock",
    "EmbeddingsRequest",
    "EmbeddingsResponse",
    "EventPublisher",
    "HealthProbe",
    "LLMProvider",
    "LockManager",
    "MetricsRecorder",
    "ModelCatalog",
    "OutboxRepository",
    "PromptRepository",
    "ProviderCallContext",
    "ProviderChatRequest",
    "ProviderChatResponse",
    "RateLimitDecision",
    "RateLimiter",
    "SecretResolver",
    "StreamChunk",
    "TenantRepository",
    "Tool",
    "ToolRegistry",
    "ToolSchema",
    "UnitOfWork",
    "UsageRepository",
]
