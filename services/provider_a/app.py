"""OpenAI-compatible mock provider for local gateway verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

API_KEY = os.environ.get("PROVIDER_A_API_KEY", "provider-a-secret")
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

app = FastAPI(title="Provider A (OpenAI-compatible)", version="1.0.0")


def _resolve_scenario(
    x_scenario: str | None,
    scenario: str | None,
) -> str:
    value = (x_scenario or scenario or "success").strip().lower()
    if value not in SCENARIOS:
        return "success"
    return value


def _verify_bearer(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != API_KEY:
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


def _embedding_vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [round(digest[i] / 255.0, 6) for i in range(8)]


def _chat_usage(messages: list[dict[str, Any]], reply: str) -> dict[str, int]:
    prompt = sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)
    completion = _estimate_tokens(reply)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def _success_chat_body(model: str, messages: list[dict[str, Any]], reply: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": _chat_usage(messages, reply),
    }


def _tool_call_chat_body(model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    reply = ""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": json.dumps({"location": "San Francisco"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": _chat_usage(messages, reply),
    }


def _refusal_chat_body(model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    reply = "I'm sorry, but I can't help with that request."
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply, "refusal": reply},
                "finish_reason": "content_filter",
            }
        ],
        "usage": _chat_usage(messages, reply),
    }


async def _apply_scenario(scenario: str) -> None:
    if scenario == "timeout":
        await asyncio.sleep(TIMEOUT_SECONDS)
    elif scenario == "delayed":
        await asyncio.sleep(DELAYED_SECONDS)


def _scenario_error_response(scenario: str):
    if scenario == "rate_limit":
        return JSONResponse(
            status_code=429,
            content={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}},
            headers={"Retry-After": "1"},
        )
    if scenario == "server_error":
        return JSONResponse(
            status_code=500,
            content={"error": {"message": "Internal server error", "type": "server_error"}},
        )
    if scenario == "model_unavailable":
        return JSONResponse(
            status_code=404,
            content={"error": {"message": "Model not found", "type": "invalid_request_error"}},
        )
    if scenario == "malformed_json":
        return PlainTextResponse(content='{"choices": [', status_code=200, media_type="application/json")
    if scenario == "empty_response":
        return PlainTextResponse(content="", status_code=200, media_type="application/json")
    return None


async def _openai_sse_stream(
    scenario: str,
    model: str,
    messages: list[dict[str, Any]],
    reply: str,
) -> AsyncIterator[str]:
    if scenario == "malformed_stream":
        yield "data: {not valid json\n\n"
        yield "data: [DONE]\n\n"
        return

    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    words = reply.split()
    for index, word in enumerate(words):
        delta = word if index == 0 else f" {word}"
        payload = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(0.01)

    if scenario == "partial_stream":
        return

    usage = _chat_usage(messages, reply)
    final_payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": usage,
    }
    yield f"data: {json.dumps(final_payload)}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": "a"}


@app.get("/__debug/requests")
async def debug_requests() -> dict[str, list[dict[str, Any]]]:
    return {"requests": list(_request_log)}


@app.get("/v1/models")
async def list_models(
    authorization: str | None = Header(default=None),
    x_scenario: str | None = Header(default=None, alias="X-Scenario"),
    scenario: str | None = Query(default=None),
):
    _verify_bearer(authorization)
    resolved = _resolve_scenario(x_scenario, scenario)
    _record_request(
        method="GET",
        path="/v1/models",
        scenario=resolved,
        headers={"authorization": "***"},
        body=None,
    )
    error = _scenario_error_response(resolved)
    if error is not None:
        return error
    await _apply_scenario(resolved)
    return {
        "object": "list",
        "data": [
            {"id": "gpt-4o-mini", "object": "model", "owned_by": "provider-a"},
            {"id": "text-embedding-3-small", "object": "model", "owned_by": "provider-a"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_scenario: str | None = Header(default=None, alias="X-Scenario"),
    scenario: str | None = Query(default=None),
):
    _verify_bearer(authorization)
    body = await request.json()
    resolved = _resolve_scenario(x_scenario, scenario)
    _record_request(
        method="POST",
        path="/v1/chat/completions",
        scenario=resolved,
        headers={"authorization": "***", "x-scenario": resolved},
        body=body,
    )

    error = _scenario_error_response(resolved)
    if error is not None:
        return error

    await _apply_scenario(resolved)

    model = str(body.get("model") or "gpt-4o-mini")
    messages = list(body.get("messages") or [])
    stream = bool(body.get("stream"))
    last_user = next(
        (str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    reply = f"Provider A reply to: {last_user or '(empty)'}"

    if resolved == "tool_call":
        payload = _tool_call_chat_body(model, messages)
        if stream:
            return StreamingResponse(
                _openai_tool_sse_stream(model, messages),
                media_type="text/event-stream",
            )
        return JSONResponse(content=payload)

    if resolved == "safety_refusal":
        payload = _refusal_chat_body(model, messages)
        if stream:
            return StreamingResponse(
                _openai_sse_stream(resolved, model, messages, payload["choices"][0]["message"]["content"]),
                media_type="text/event-stream",
            )
        return JSONResponse(content=payload)

    if stream:
        return StreamingResponse(
            _openai_sse_stream(resolved, model, messages, reply),
            media_type="text/event-stream",
        )

    return JSONResponse(content=_success_chat_body(model, messages, reply))


async def _openai_tool_sse_stream(
    model: str,
    messages: list[dict[str, Any]],
) -> AsyncIterator[str]:
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    tool_payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": ""},
                        }
                    ],
                },
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(tool_payload)}\n\n"

    args_payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": json.dumps({"location": "San Francisco"})},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(args_payload)}\n\n"

    usage = _chat_usage(messages, "")
    final_payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        "usage": usage,
    }
    yield f"data: {json.dumps(final_payload)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/embeddings")
async def embeddings(
    request: Request,
    authorization: str | None = Header(default=None),
    x_scenario: str | None = Header(default=None, alias="X-Scenario"),
    scenario: str | None = Query(default=None),
):
    _verify_bearer(authorization)
    body = await request.json()
    resolved = _resolve_scenario(x_scenario, scenario)
    _record_request(
        method="POST",
        path="/v1/embeddings",
        scenario=resolved,
        headers={"authorization": "***", "x-scenario": resolved},
        body=body,
    )

    error = _scenario_error_response(resolved)
    if error is not None:
        return error

    await _apply_scenario(resolved)

    raw_input = body.get("input")
    if isinstance(raw_input, str):
        inputs = [raw_input]
    elif isinstance(raw_input, list):
        inputs = [str(item) for item in raw_input]
    else:
        inputs = [""]

    model = str(body.get("model") or "text-embedding-3-small")
    prompt_tokens = sum(_estimate_tokens(text) for text in inputs)
    data = [
        {"object": "embedding", "index": index, "embedding": _embedding_vector(text)}
        for index, text in enumerate(inputs)
    ]
    return JSONResponse(
        content={
            "object": "list",
            "data": data,
            "model": model,
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        }
    )
