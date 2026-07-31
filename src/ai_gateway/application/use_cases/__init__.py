"""Use cases: one class per business operation the gateway exposes."""

from __future__ import annotations

from ai_gateway.application.use_cases.agent_run import RunAgentUseCase
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.catalog import (
    ListModelsUseCase,
    ListProvidersUseCase,
)
from ai_gateway.application.use_cases.chat_completion import ChatCompletionUseCase
from ai_gateway.application.use_cases.embeddings import EmbeddingsUseCase
from ai_gateway.application.use_cases.prompts import (
    GetPromptUseCase,
    ListPromptsUseCase,
    PublishPromptUseCase,
)
from ai_gateway.application.use_cases.usage import GetUsageReportUseCase

__all__ = [
    "ChatCompletionUseCase",
    "EmbeddingsUseCase",
    "GatewayServices",
    "GetPromptUseCase",
    "GetUsageReportUseCase",
    "ListModelsUseCase",
    "ListPromptsUseCase",
    "ListProvidersUseCase",
    "PublishPromptUseCase",
    "RunAgentUseCase",
]
