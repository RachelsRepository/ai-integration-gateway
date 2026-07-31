"""Mapping helpers between API schemas and application DTOs."""

from __future__ import annotations

import json
from typing import Any

from ai_gateway.api.schemas import (
    AgentRunRequest,
    ChatCompletionRequest,
    EmbeddingsRequest,
    PromptPublishRequest,
    UsageSchema,
)
from ai_gateway.application.dto import (
    AgentRunCommand,
    ChatCompletionCommand,
    ChatCompletionResult,
    EmbeddingsCommand,
    EmbeddingsResult,
    PromptPublishCommand,
    ToolSpecDTO,
)
from ai_gateway.domain.entities.message import Message, MessageRole, ToolCall
from ai_gateway.domain.policies.routing import RoutingStrategy
from ai_gateway.domain.value_objects.identifiers import ConversationId
from ai_gateway.domain.value_objects.tokens import TokenUsage


def usage_schema(usage: TokenUsage) -> UsageSchema:
    """Map token usage to the API schema."""
    return UsageSchema(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cached_prompt_tokens=usage.cached_prompt_tokens,
        reasoning_tokens=usage.reasoning_tokens,
    )


def to_chat_command(body: ChatCompletionRequest) -> ChatCompletionCommand:
    """Map a chat request body to a use-case command."""
    messages: list[Message] = []
    for item in body.messages:
        tool_calls = tuple(
            ToolCall(
                id=str(call.get("id") or ""),
                name=str((call.get("function") or call).get("name") or call.get("name") or ""),
                arguments=_parse_arguments(call),
            )
            for call in item.tool_calls
        )
        messages.append(
            Message(
                role=MessageRole(item.role),
                content=item.content,
                name=item.name,
                tool_call_id=item.tool_call_id,
                tool_calls=tool_calls,
            )
        )
    return ChatCompletionCommand(
        messages=tuple(messages),
        model=body.model,
        prompt_name=body.prompt_name,
        prompt_version=body.prompt_version,
        prompt_variables=body.prompt_variables,
        conversation_id=ConversationId(body.conversation_id) if body.conversation_id else None,
        max_output_tokens=body.max_tokens,
        temperature=body.temperature,
        top_p=body.top_p,
        stop=tuple(body.stop),
        stream=body.stream,
        tools=tuple(
            ToolSpecDTO(name=t.name, description=t.description, parameters=t.parameters)
            for t in body.tools
        ),
        tool_choice=body.tool_choice,
        response_format=body.response_format,
        routing_strategy=RoutingStrategy(body.routing_strategy),
        allow_fallback=body.allow_fallback,
        cache=body.cache,
        seed=body.seed,
        metadata=body.metadata,
    )


def chat_result_to_response(result: ChatCompletionResult) -> dict[str, Any]:
    """Map a chat result to an OpenAI-compatible response body."""
    message: dict[str, Any] = {
        "role": result.message.role.value,
        "content": result.message.content,
    }
    if result.message.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in result.message.tool_calls
        ]
    return {
        "id": str(result.request_id),
        "object": "chat.completion",
        "created": int(result.created_at.timestamp()),
        "model": result.model.qualified,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": result.finish_reason.value,
            }
        ],
        "usage": usage_schema(result.usage).model_dump(),
        "cost_micros": result.cost.micros,
        "currency": result.cost.currency,
        "latency_ms": result.latency_ms,
        "cached": result.cached,
        "conversation_id": result.conversation_id,
        "routing": {
            "selected_model": result.routing.selected_model,
            "strategy": result.routing.strategy.value,
            "reason": result.routing.reason,
            "attempts": list(result.routing.attempts),
            "fallback_used": result.routing.fallback_used,
            "rejected": result.routing.rejected,
        },
    }


def to_embeddings_command(body: EmbeddingsRequest) -> EmbeddingsCommand:
    """Map an embeddings request body to a use-case command."""
    inputs = (body.input,) if isinstance(body.input, str) else tuple(body.input)
    return EmbeddingsCommand(
        inputs=inputs,
        model=body.model,
        dimensions=body.dimensions,
        cache=body.cache,
        routing_strategy=RoutingStrategy(body.routing_strategy),
        metadata=body.metadata,
    )


def embeddings_result_to_response(result: EmbeddingsResult) -> dict[str, Any]:
    """Map an embeddings result to the API response body."""
    return {
        "object": "list",
        "model": result.model.qualified,
        "data": [
            {"object": "embedding", "index": index, "embedding": list(vector)}
            for index, vector in enumerate(result.vectors)
        ],
        "usage": usage_schema(result.usage).model_dump(),
        "cost_micros": result.cost.micros,
        "currency": result.cost.currency,
        "latency_ms": result.latency_ms,
        "cache_hits": result.cache_hits,
    }


def to_agent_command(body: AgentRunRequest) -> AgentRunCommand:
    """Map an agent run request body to a use-case command."""
    return AgentRunCommand(
        input=body.input,
        agent_name=body.agent_name,
        instructions=body.instructions,
        tools=tuple(body.tools),
        model=body.model,
        max_iterations=body.max_iterations,
        conversation_id=ConversationId(body.conversation_id) if body.conversation_id else None,
        temperature=body.temperature,
        max_output_tokens=body.max_output_tokens,
        metadata=body.metadata,
    )


def to_prompt_command(body: PromptPublishRequest) -> PromptPublishCommand:
    """Map a prompt publish request body to a use-case command."""
    return PromptPublishCommand(
        name=body.name,
        template=body.template,
        description=body.description,
        system_prompt=body.system_prompt,
        safety_prompt=body.safety_prompt,
        required_variables=(
            frozenset(body.required_variables) if body.required_variables is not None else None
        ),
        activate=body.activate,
        notes=body.notes,
        labels=body.labels,
    )


def _parse_arguments(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") or call
    arguments = function.get("arguments") or call.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"_raw": arguments}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return dict(arguments) if isinstance(arguments, dict) else {}


__all__ = [
    "chat_result_to_response",
    "embeddings_result_to_response",
    "to_agent_command",
    "to_chat_command",
    "to_embeddings_command",
    "to_prompt_command",
    "usage_schema",
]
