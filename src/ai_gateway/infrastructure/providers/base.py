"""Shared helpers for HTTP-backed provider adapters."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from ai_gateway.application.ports.llm_provider import ToolSchema
from ai_gateway.domain.entities.message import FinishReason, Message, MessageRole, ToolCall
from ai_gateway.domain.errors import (
    ProviderAuthenticationError,
    ProviderBadResponseError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from ai_gateway.domain.value_objects.provider import ProviderName


def map_http_error(provider: ProviderName, response: httpx.Response) -> ProviderError:
    """Map an HTTP error response onto a domain error.

    Args:
        provider: Provider that returned the error.
        response: HTTP response.

    Returns:
        The corresponding domain error.
    """
    status = response.status_code
    body = _safe_body(response)
    message = f"{provider.value} returned HTTP {status}"
    details = {"status": status, "body": body}
    if status in {401, 403}:
        return ProviderAuthenticationError(message, provider=provider.value, details=details)
    if status == 429:
        return ProviderRateLimitError(message, provider=provider.value, details=details)
    if status >= 500:
        return ProviderError(message, provider=provider.value, details=details)
    return ProviderBadResponseError(message, provider=provider.value, details=details)


def map_transport_error(provider: ProviderName, exc: Exception) -> ProviderError:
    """Map a transport exception onto a domain error.

    Args:
        provider: Provider being called.
        exc: Transport exception.

    Returns:
        The corresponding domain error.
    """
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(str(exc) or "Provider timed out", provider=provider.value)
    if isinstance(exc, httpx.HTTPStatusError):
        return map_http_error(provider, exc.response)
    return ProviderError(str(exc) or "Provider call failed", provider=provider.value)


def messages_to_openai(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    """Serialise messages into the OpenAI chat format.

    Args:
        messages: Domain messages.

    Returns:
        OpenAI-shaped message dictionaries.
    """
    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role.value, "content": message.content}
        if message.name:
            item["name"] = message.name
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ]
        payload.append(item)
    return payload


def tools_to_openai(tools: tuple[ToolSchema, ...]) -> list[dict[str, Any]]:
    """Serialise tool schemas into the OpenAI tools format.

    Args:
        tools: Tool schemas.

    Returns:
        OpenAI-shaped tool dictionaries.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def parse_openai_tool_calls(raw: list[dict[str, Any]] | None) -> tuple[ToolCall, ...]:
    """Parse OpenAI-style tool calls into domain tool calls.

    Args:
        raw: Provider tool_calls array.

    Returns:
        Domain tool calls.
    """
    if not raw:
        return ()
    calls: list[ToolCall] = []
    for item in raw:
        function = item.get("function") or {}
        arguments = function.get("arguments") or "{}"
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                parsed = {"_raw": arguments}
        else:
            parsed = dict(arguments)
        calls.append(
            ToolCall(
                id=str(item.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=parsed if isinstance(parsed, dict) else {"value": parsed},
            )
        )
    return tuple(calls)


def parse_finish_reason(raw: str | None) -> FinishReason:
    """Normalise a provider finish reason.

    Args:
        raw: Provider-specific finish reason.

    Returns:
        The normalised finish reason.
    """
    if raw is None:
        return FinishReason.STOP
    mapping = {
        "stop": FinishReason.STOP,
        "end_turn": FinishReason.STOP,
        "STOP": FinishReason.STOP,
        "length": FinishReason.LENGTH,
        "max_tokens": FinishReason.LENGTH,
        "tool_calls": FinishReason.TOOL_CALLS,
        "tool_use": FinishReason.TOOL_CALLS,
        "content_filter": FinishReason.CONTENT_FILTER,
        "safety": FinishReason.CONTENT_FILTER,
    }
    return mapping.get(raw, FinishReason.STOP)


def split_system_messages(
    messages: tuple[Message, ...],
) -> tuple[str | None, list[Message]]:
    """Extract concatenated system content and the remaining transcript.

    Args:
        messages: Domain messages.

    Returns:
        A tuple of optional system text and the non-system messages.
    """
    system_parts = [m.content for m in messages if m.role is MessageRole.SYSTEM]
    rest = [m for m in messages if m.role is not MessageRole.SYSTEM]
    system = "\n\n".join(system_parts) if system_parts else None
    return system, rest


def _safe_body(response: httpx.Response) -> str:
    try:
        text = response.text
    except Exception:
        return ""
    return text[:2_000]


async def iter_sse_lines(response: httpx.Response) -> AsyncIterator[str]:
    """Yield data payloads from an SSE response.

    Args:
        response: Streaming HTTP response.

    Yields:
        The ``data:`` payload of each event.
    """
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        yield data


def require_mapping(payload: Any, provider: ProviderName) -> Mapping[str, Any]:
    """Assert that a JSON payload is an object.

    Args:
        payload: Parsed JSON.
        provider: Provider for error attribution.

    Returns:
        The mapping.

    Raises:
        ProviderBadResponseError: If the payload is not an object.
    """
    if not isinstance(payload, Mapping):
        raise ProviderBadResponseError(
            "Provider returned a non-object JSON payload",
            provider=provider.value,
        )
    return payload


__all__ = [
    "iter_sse_lines",
    "map_http_error",
    "map_transport_error",
    "messages_to_openai",
    "parse_finish_reason",
    "parse_openai_tool_calls",
    "require_mapping",
    "split_system_messages",
    "tools_to_openai",
]
