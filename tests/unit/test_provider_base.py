"""Provider base helper tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

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
from ai_gateway.infrastructure.providers.base import (
    iter_sse_lines,
    map_http_error,
    map_transport_error,
    messages_to_openai,
    parse_finish_reason,
    parse_openai_tool_calls,
    require_mapping,
    split_system_messages,
    tools_to_openai,
)


def test_map_http_error_status_codes() -> None:
    for status, expected in [
        (401, ProviderAuthenticationError),
        (429, ProviderRateLimitError),
        (500, ProviderError),
        (400, ProviderBadResponseError),
    ]:
        response = httpx.Response(status, json={"error": "x"})
        err = map_http_error(ProviderName.OPENAI, response)
        assert isinstance(err, expected)


def test_map_transport_error_timeout() -> None:
    err = map_transport_error(ProviderName.OPENAI, httpx.ReadTimeout("slow"))
    assert isinstance(err, ProviderTimeoutError)


def test_map_transport_error_http_status() -> None:
    request = httpx.Request("GET", "http://example.com")
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("fail", request=request, response=response)
    err = map_transport_error(ProviderName.OPENAI, exc)
    assert isinstance(err, ProviderError)


def test_messages_to_openai_serialises_tools() -> None:
    message = Message(
        role=MessageRole.ASSISTANT,
        content="",
        tool_calls=(ToolCall(id="c1", name="calc", arguments={"x": 1}),),
    )
    payload = messages_to_openai((Message.system("sys"), Message.user("hi"), message))
    assert payload[0]["role"] == "system"
    assert payload[2]["tool_calls"][0]["function"]["name"] == "calc"


def test_tools_to_openai() -> None:
    tools = tools_to_openai((ToolSchema(name="search", description="Search"),))
    assert tools[0]["function"]["name"] == "search"


def test_parse_openai_tool_calls_handles_bad_json() -> None:
    calls = parse_openai_tool_calls(
        [{"id": "1", "function": {"name": "x", "arguments": "not-json"}}]
    )
    assert calls[0].arguments == {"_raw": "not-json"}


def test_parse_finish_reason_mapping() -> None:
    assert parse_finish_reason("length") is FinishReason.LENGTH
    assert parse_finish_reason("tool_use") is FinishReason.TOOL_CALLS
    assert parse_finish_reason(None) is FinishReason.STOP


def test_split_system_messages() -> None:
    system, rest = split_system_messages(
        (Message.system("a"), Message.system("b"), Message.user("hi"))
    )
    assert system == "a\n\nb"
    assert len(rest) == 1


def test_require_mapping_rejects_non_object() -> None:
    with pytest.raises(ProviderBadResponseError):
        require_mapping([1, 2], ProviderName.OPENAI)


@pytest.mark.asyncio
async def test_iter_sse_lines() -> None:
    async def _lines():
        for line in ('data: {"x": 1}', "", "data: [DONE]"):
            yield line

    response = MagicMock()
    response.aiter_lines = _lines
    lines = [line async for line in iter_sse_lines(response)]
    assert json.loads(lines[0])["x"] == 1
