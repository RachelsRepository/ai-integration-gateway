# API guide

Interactive docs are available at `/docs` and `/redoc` when `AIGW_DOCS_ENABLED=true`.
The machine-readable contract is exported to `openapi.json` via:

```bash
make openapi
```

## Authentication

Provide one of:

- `X-API-Key: <key>`
- `Authorization: Bearer <jwt>`

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | Chat completion (JSON or SSE) |
| POST | `/v1/responses` | Alias of chat completions |
| POST | `/v1/embeddings` | Embedding vectors |
| POST | `/v1/agents/run` | Agent execution with tools |
| POST | `/v1/prompts` | Publish a prompt version |
| GET | `/v1/prompts` | List prompts |
| GET | `/v1/prompts/{name}` | Get prompt history |
| GET | `/v1/models` | List routable models |
| GET | `/v1/providers` | List providers and health |
| GET | `/health/live` | Liveness |
| GET | `/health/ready` | Readiness |
| GET | `/metrics` | Prometheus scrape |

## Streaming

Set `"stream": true` on chat completions. The response is `text/event-stream` with
events `start`, `delta`, `tool_call`, `usage`, `done` and `error`, terminated by
`data: [DONE]`.

## Error envelope

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Request rate limit exceeded",
    "details": {"limit": 600, "remaining": 0}
  }
}
```

Rate-limit responses include `Retry-After`.
