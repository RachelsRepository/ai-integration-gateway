#!/usr/bin/env bash
# Live provider failure matrix through the gateway using X-Scenario (test mode).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${API_BASE:-http://127.0.0.1:18000}"
KEY="${AIGW_API_KEY:-aigw_local_demo_key_do_not_use_in_prod_001}"
AUTH=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")

curl -sf "$API/health/ready" >/dev/null
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true

expect_not_200() {
  local scenario="$1" model="$2"
  curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true
  code="$(curl -s -o /tmp/aigw_matrix.json -w '%{http_code}' "$API/v1/chat/completions" "${AUTH[@]}" \
    -H "X-Scenario: $scenario" \
    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"$scenario\"}],\"model\":\"$model\",\"temperature\":0,\"cache\":false}")"
  if [[ "$code" == "200" ]]; then
    echo "expected non-200 for scenario=$scenario model=$model got 200" >&2
    cat /tmp/aigw_matrix.json >&2 || true
    exit 1
  fi
  echo "  $scenario -> HTTP $code"
}

echo "==> Provider A (openai schema) matrix"
for scenario in timeout rate_limit server_error malformed_json empty_response model_unavailable; do
  expect_not_200 "$scenario" "openai/gpt-4o-mini"
done

echo "==> Provider A success + streaming"
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true
curl -sf "$API/v1/chat/completions" "${AUTH[@]}" \
  -H "X-Scenario: success" \
  -d '{"messages":[{"role":"user","content":"ok"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}' >/dev/null
STREAM="$(curl -sf -N "$API/v1/chat/completions" "${AUTH[@]}" \
  -H "X-Scenario: success" \
  -d '{"messages":[{"role":"user","content":"stream"}],"model":"openai/gpt-4o-mini","stream":true,"temperature":0,"cache":false}')"
echo "$STREAM" | grep -q 'event: start'
echo "$STREAM" | grep -q 'event: done'

echo "==> Provider A partial stream failure"
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true
code="$(curl -s -o /tmp/aigw_partial.txt -w '%{http_code}' -N "$API/v1/chat/completions" "${AUTH[@]}" \
  -H "X-Scenario: partial_stream" \
  -d '{"messages":[{"role":"user","content":"partial"}],"model":"openai/gpt-4o-mini","stream":true,"temperature":0,"cache":false}' || true)"
# Streaming may return 200 with an error event; accept either non-200 or error event.
if [[ "$code" == "200" ]]; then
  grep -E 'event: error|finish_reason|error' /tmp/aigw_partial.txt >/dev/null \
    || { echo "partial stream did not surface failure" >&2; cat /tmp/aigw_partial.txt >&2; exit 1; }
fi

echo "==> Provider B (anthropic schema) matrix"
for scenario in timeout rate_limit server_error malformed_json; do
  expect_not_200 "$scenario" "anthropic/claude-3-5-haiku-latest"
done
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true
curl -sf "$API/v1/chat/completions" "${AUTH[@]}" \
  -H "X-Scenario: success" \
  -d '{"messages":[{"role":"user","content":"ok-b"}],"model":"anthropic/claude-3-5-haiku-latest","temperature":0,"cache":false}' >/dev/null

echo "==> Embeddings still healthy on A"
curl -sf "$API/v1/embeddings" "${AUTH[@]}" \
  -d '{"model":"openai/text-embedding-3-small","input":["matrix"]}' >/dev/null

echo "PROVIDER MATRIX OK"
