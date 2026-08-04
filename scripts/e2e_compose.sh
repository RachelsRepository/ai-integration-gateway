#!/usr/bin/env bash
# Compose end-to-end verification against live fictional providers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${API_BASE:-http://127.0.0.1:18000}"
KEY="${AIGW_API_KEY:-aigw_local_demo_key_do_not_use_in_prod_001}"
AUTH=(-H "X-API-Key: $KEY" -H "Content-Type: application/json" -H "Connection: close")

json_post() {
  local url="$1"
  shift
  local body_file
  body_file="$(mktemp)"
  local code
  code="$(curl -sS -o "$body_file" -w '%{http_code}' --connect-timeout 5 --max-time 60 \
    "${AUTH[@]}" "$@" "$url" || echo 000)"
  if [[ "$code" != "200" ]]; then
    echo "HTTP $code from $url" >&2
    head -c 2000 "$body_file" >&2 || true
    echo >&2
    rm -f "$body_file"
    return 1
  fi
  cat "$body_file"
  rm -f "$body_file"
}

echo "==> Waiting for stack readiness (API + providers)"
API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"

echo "==> Models"
json_post "$API/v1/models" -X GET | python3 -c 'import sys,json; d=json.load(sys.stdin); assert len(d)>=1'

echo "==> Chat (echo)"
json_post "$API/v1/chat/completions" -X POST \
  -d '{"messages":[{"role":"user","content":"hello"}],"model":"echo/echo-1","temperature":0,"cache":false}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); assert "echo" in (d.get("choices") or [{}])[0].get("message",{}).get("content","")'

echo "==> Chat (provider A / openai)"
json_post "$API/v1/chat/completions" -X POST \
  -d '{"messages":[{"role":"user","content":"ping"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("model"); assert (d.get("choices") or [{}])[0].get("message",{}).get("content"); assert d.get("usage")'

echo "==> Chat (provider B / anthropic schema)"
json_post "$API/v1/chat/completions" -X POST \
  -d '{"messages":[{"role":"user","content":"ping"}],"model":"anthropic/claude-3-5-haiku-latest","temperature":0,"cache":false}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); assert (d.get("choices") or [{}])[0].get("message",{}).get("content")'

echo "==> Streaming"
STREAM="$(curl -sS -N --connect-timeout 5 --max-time 60 "${AUTH[@]}" \
  -d '{"messages":[{"role":"user","content":"stream"}],"model":"openai/gpt-4o-mini","stream":true,"temperature":0,"cache":false}' \
  "$API/v1/chat/completions")"
echo "$STREAM" | grep -q 'event: start'
echo "$STREAM" | grep -q 'event: done'

echo "==> Embeddings (provider A)"
json_post "$API/v1/embeddings" -X POST \
  -d '{"model":"openai/text-embedding-3-small","input":["hello"]}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); data=d.get("data") or d.get("embeddings") or []; assert len(data)>=1, d'

echo "==> Provider A live scenario via gateway (X-Scenario forwarding)"
code="$(curl -sS -o /tmp/aigw_gw_rl.json -w '%{http_code}' --connect-timeout 5 --max-time 60 \
  "${AUTH[@]}" -H "X-Scenario: rate_limit" \
  -d '{"messages":[{"role":"user","content":"rl"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}' \
  "$API/v1/chat/completions" || echo 000)"
[[ "$code" != "200" ]]

echo "==> Provider A direct scenario still works"
code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 30 \
  -H "Authorization: Bearer provider-a-secret" -H "X-Scenario: rate_limit" \
  -H "Content-Type: application/json" -H "Connection: close" \
  http://127.0.0.1:18001/v1/chat/completions \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"rl"}]}' || echo 000)"
[[ "$code" == "429" ]]

echo "==> Unauthenticated rejected"
code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 -H 'Connection: close' "$API/v1/models" || echo 000)"
[[ "$code" == "401" || "$code" == "403" ]]

echo "==> Metrics endpoint"
curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 -H 'Connection: close' "$API/metrics" | grep -q 200

echo "==> Persistence survives API recreate"
docker compose restart api >/dev/null
API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
# Demo key is durable in Postgres; authenticated call with the original key must still work.
json_post "$API/v1/models" -X GET >/dev/null
json_post "$API/v1/embeddings" -X POST \
  -d '{"model":"openai/text-embedding-3-small","input":["after-restart"]}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); assert (d.get("data") or d.get("embeddings") or [])'

echo "E2E OK"
