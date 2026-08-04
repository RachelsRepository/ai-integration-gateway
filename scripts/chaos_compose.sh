#!/usr/bin/env bash
# Deterministic chaos subset against a running Compose stack.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${API_BASE:-http://127.0.0.1:18000}"
KEY="${AIGW_API_KEY:-aigw_local_demo_key_do_not_use_in_prod_001}"
AUTH=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")

echo "==> Baseline ready"
curl -sf "$API/health/ready" >/dev/null
API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true

echo "==> Pause Redis (readiness should degrade)"
docker compose pause redis
sleep 2
ready_code="$(curl -s -o /tmp/aigw_ready.json -w '%{http_code}' "$API/health/ready" || true)"
if [[ "$ready_code" == "200" ]]; then
  python3 -c 'import json,sys; d=json.load(open("/tmp/aigw_ready.json")); sys.exit(0 if d.get("status")!="ok" else 1)' \
    || { echo "readiness stayed ok while Redis paused" >&2; cat /tmp/aigw_ready.json >&2; exit 1; }
fi
docker compose unpause redis
API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
curl -sf "$API/health/ready" >/dev/null

echo "==> Provider A outage (stop) then recovery"
docker compose stop provider-a >/dev/null
sleep 2
# Chat to provider A should fail closed (not 200 success).
code="$(curl -s -o /dev/null -w '%{http_code}' "$API/v1/chat/completions" "${AUTH[@]}" \
  -d '{"messages":[{"role":"user","content":"outage"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}')"
[[ "$code" != "200" ]]
docker compose start provider-a >/dev/null
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:18001/health >/dev/null && break
  sleep 2
done
API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true
curl -sf "$API/v1/chat/completions" "${AUTH[@]}" \
  -d '{"messages":[{"role":"user","content":"recovered"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}' >/dev/null

echo "==> Provider B outage"
docker compose stop provider-b >/dev/null
sleep 2
code="$(curl -s -o /dev/null -w '%{http_code}' "$API/v1/chat/completions" "${AUTH[@]}" \
  -d '{"messages":[{"role":"user","content":"outage-b"}],"model":"anthropic/claude-3-5-haiku-latest","temperature":0,"cache":false}')"
[[ "$code" != "200" ]]
docker compose start provider-b >/dev/null
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:18002/health >/dev/null && break
  sleep 2
done
API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true

echo "==> Full stack restart recovers"
docker compose restart api worker >/dev/null
API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
curl -sf "$API/v1/models" "${AUTH[@]}" >/dev/null
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true

echo "CHAOS OK"
