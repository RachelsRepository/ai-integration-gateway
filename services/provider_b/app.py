"""Anthropic-style mock provider for local gateway verification."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

API_KEY = os.environ.get("PROVIDER_B_API_KEY", "provider-b-secret")
MAX_DEBUG_REQUESTS = 50
DELAYED_SECONDS = 2.0
TIMEOUT_SECONDS = 3600.0

SCENARIOS = frozenset(
    {
        "success",
        "timeout",
        "rate_limit",
        "server_error",
        "malformed_json",
        "malformed_stream",
        "partial_stream",
        "empty_response",
        "model_unavailable",
        "tool_call",
        "delayed",
        "safety_refusal",
    }
)

_request_log: deque[dict[str, Any]] = deque(maxlen=MAX_DEBUG_REQUESTS)

app = FastAPI(title="Provider B (Anthropic-style)", version="1.0.0")


def _resolve_scenario(
    x_scenario: str | None,
    scenario: str | None,
) -> str:
    value = (x_scenario or scenario or "success").strip().lower()
    if value not in SCENARIOS:
        return "success"
    return value


def _verify_api_key(x_api_key: str | None) -> None:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _record_request(
    *,
    method: str,
    path: str,
    scenario: str,
    headers: dict[str, str],
    body: Any,
) -> None:
    _request_log.appendleft(
        {
            "timestamp": time.time(),
            "method": method,
            "path": path,
            "scenario": scenario,
            "headers": headers,
            "body": body,
        }
    )


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


def _message_usage(messages: list[dict[str, Any]], reply: str) -> dict[str, int]:
    prompt = sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)
    completion = _estimate_tokens(reply)
    return {"input_tokens": prompt, "output_tokens": completion}


def _success_message_body(
    model: str,
    messages: list[dict[str, Any]],
    reply: str,
    *,
    stop_reason: str = "end_turn",
) -> dict[str, Any]:
    return {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": reply}],
        "stop_reason": stop_reason,
        "usage": _message_usage(messages, reply),
    }


def _tool_call_message_body(model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:8]}",
                "name": "get_weather",
                "input": {"location": "San Francisco"},
            }
        ],
        "stop_reason": "tool_use",
        "usage": _message_usage(messages, ""),
    }


def _refusal_message_body(model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    reply = "I'm sorry, but I can't help with that request."
    return _success_message_body(model, messages, reply, stop_reason="end_turn")


async def _apply_scenario(scenario: str) -> None:
    if scenario == "timeout":
        await asyncio.sleep(TIMEOUT_SECONDS)
    elif scenario == "delayed":
        await asyncio.sleep(DELAYED_SECONDS)


def _scenario_error_response(scenario: str):
    if scenario == "rate_limit":
        return JSONResponse(
            status_code=429,
            content={"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limited"}},
            headers={"Retry-After": "1"},
        )
    if scenario == "server_error":
        return JSONResponse(
            status_code=500,
            content={"type": "error", "error": {"type": "api_error", "message": "Internal server error"}},
        )
    if scenario == "model_unavailable":
        return JSONResponse(
            status_code=404,
            content={"type": "error", "error": {"type": "not_found_error", "message": "Model not found"}},
        )
    if scenario == "malformed_json":
        return PlainTextResponse(content='{"content": [', status_code=200, media_type="application/json")
    if scenario == "empty_response":
        return PlainTextResponse(content="", status_code=200, media_type="application/json")
    return None


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _anthropic_sse_stream(
    scenario: str,
    model: str,
    messages: list[dict[str, Any]],
    reply: str,
) -> AsyncIterator[str]:
    if scenario == "malformed_stream":
        yield "event: content_block_delta\ndata: {broken\n\n"
        return

    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    usage = _message_usage(messages, reply)
    yield _sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 0},
            },
        },
    )

    words = reply.split()
    for index, word in enumerate(words):
        text = word if index == 0 else f" {word}"
        yield _sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        )
        await asyncio.sleep(0.01)

    if scenario == "partial_stream":
        return

    yield _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": usage["output_tokens"]},
        },
    )
    yield _sse_event("message_stop", {"type": "message_stop"})


async def _anthropic_tool_sse_stream(model: str, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    usage = _message_usage(messages, "")
    tool_id = f"toolu_{uuid.uuid4().hex[:8]}"
    yield _sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 0},
            },
        },
    )
    yield _sse_event(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": tool_id, "name": "get_weather", "input": {}},
        },
    )
    yield _sse_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"location": "San Francisco"}'},
        },
    )
    yield _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 1},
        },
    )
    yield _sse_event("message_stop", {"type": "message_stop"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": "b"}


@app.get("/__debug/requests")
async def debug_requests() -> dict[str, list[dict[str, Any]]]:
    return {"requests": list(_request_log)}


@app.post("/v1/messages")
async def messages(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_scenario: str | None = Header(default=None, alias="X-Scenario"),
    scenario: str | None = Query(default=None),
):
    _verify_api_key(x_api_key)
    body = await request.json()
    resolved = _resolve_scenario(x_scenario, scenario)
    _record_request(
        method="POST",
        path="/v1/messages",
        scenario=resolved,
        headers={"x-api-key": "***", "x-scenario": resolved},
        body=body,
    )

    error = _scenario_error_response(resolved)
    if error is not None:
        return error

    await _apply_scenario(resolved)

    model = str(body.get("model") or "claude-3-5-haiku-latest")
    raw_messages = list(body.get("messages") or [])
    stream = bool(body.get("stream"))
    last_user = next(
        (str(m.get("content") or "") for m in reversed(raw_messages) if m.get("role") == "user"),
        "",
    )
    reply = f"Provider B reply to: {last_user or '(empty)'}"

    if resolved == "tool_call":
        payload = _tool_call_message_body(model, raw_messages)
        if stream:
            return StreamingResponse(
                _anthropic_tool_sse_stream(model, raw_messages),
                media_type="text/event-stream",
            )
        return JSONResponse(content=payload)

    if resolved == "safety_refusal":
        payload = _refusal_message_body(model, raw_messages)
        if stream:
            return StreamingResponse(
                _anthropic_sse_stream(resolved, model, raw_messages, payload["content"][0]["text"]),
                media_type="text/event-stream",
            )
        return JSONResponse(content=payload)

    if stream:
        return StreamingResponse(
            _anthropic_sse_stream(resolved, model, raw_messages, reply),
            media_type="text/event-stream",
        )

    return JSONResponse(content=_success_message_body(model, raw_messages, reply))


@app.api_route("/v1/embeddings", methods=["GET", "POST"])
@app.api_route("/v1/models", methods=["GET"])
async def unsupported() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"type": "error", "error": {"type": "not_found_error", "message": "Not supported"}},
    )
