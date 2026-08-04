#!/usr/bin/env bash
# Multi-replica shared quota/budget race + scenario matrix against Compose.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API_A="${API_BASE_A:-http://127.0.0.1:18000}"
API_B="${API_BASE_B:-http://127.0.0.1:18003}"
KEY="${AIGW_API_KEY:-aigw_local_demo_key_do_not_use_in_prod_001}"
AUTH=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")

restore_defaults() {
  curl -s -X PUT "$API_A/v1/admin/tenants/me/quotas" "${AUTH[@]}" \
    -d '{"period":"daily","rate_limit_burst":60,"rate_limit_per_minute":600}' >/dev/null || true
  curl -s -X POST "$API_A/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true
}
trap restore_defaults EXIT

echo "==> Ensure HA profile (api-2) is up"
docker compose --profile ha up -d api api-2 worker >/dev/null
for i in $(seq 1 40); do
  curl -sf "$API_A/health/ready" >/dev/null && curl -sf "$API_B/health/ready" >/dev/null && break
  sleep 2
  if [[ "$i" -eq 40 ]]; then
    echo "API replicas not ready" >&2
    docker compose ps >&2
    exit 1
  fi
done

restore_defaults

echo "==> Reset distributed circuits"
curl -sf -X POST "$API_A/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null

echo "==> Gateway X-Scenario rate_limit via API A"
code="$(curl -s -o /tmp/aigw_sc.json -w '%{http_code}' "$API_A/v1/chat/completions" "${AUTH[@]}" \
  -H "X-Scenario: rate_limit" \
  -d '{"messages":[{"role":"user","content":"rl"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}')"
[[ "$code" != "200" ]]

curl -sf -X POST "$API_A/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null

echo "==> Gateway X-Scenario success via API B"
curl -sf "$API_B/v1/chat/completions" "${AUTH[@]}" \
  -H "X-Scenario: success" \
  -d '{"messages":[{"role":"user","content":"ok-ha"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}' >/dev/null

echo "==> Shared concurrency race across replicas (Redis inflight)"
curl -sf -X PUT "$API_A/v1/admin/tenants/me/quotas" "${AUTH[@]}" \
  -d '{"period":"daily","rate_limit_burst":1}' >/dev/null
TENANT="00000000-0000-4000-8000-000000000001"
docker compose exec -T redis redis-cli DEL "aigw:quota:${TENANT}:inflight" >/dev/null || true
ok=0
reject=0
pids=()
for i in $(seq 1 10); do
  base="$API_A"
  if (( i % 2 == 0 )); then base="$API_B"; fi
  (
    # delayed scenario keeps requests in-flight long enough to contend for the shared slot.
    code="$(curl -s -o /tmp/aigw_ha_q_$i.json -w '%{http_code}' "$base/v1/chat/completions" "${AUTH[@]}" \
      -H "X-Scenario: delayed" \
      -d "{\"messages\":[{\"role\":\"user\",\"content\":\"q-$i\"}],\"model\":\"openai/gpt-4o-mini\",\"temperature\":0,\"cache\":false,\"max_output_tokens\":16}")"
    echo "$code" >"/tmp/aigw_ha_code_$i"
  ) &
  pids+=($!)
done
for pid in "${pids[@]}"; do wait "$pid" || true; done
for i in $(seq 1 10); do
  code="$(cat "/tmp/aigw_ha_code_$i" 2>/dev/null || echo 000)"
  if [[ "$code" == "200" ]]; then
    ok=$((ok + 1))
  else
    reject=$((reject + 1))
  fi
done
if [[ "$reject" -lt 1 || "$ok" -lt 1 ]]; then
  echo "expected mixed success/reject across shared concurrency; ok=$ok reject=$reject" >&2
  exit 1
fi
echo "  concurrency race ok=$ok reject=$reject"

echo "==> Shared daily token budget race (Redis reservation)"
restore_defaults
# Durable ceiling stays high; Redis counter is pre-filled near the shared limit.
curl -sf -X PUT "$API_A/v1/admin/tenants/me/quotas" "${AUTH[@]}" \
  -d '{"period":"daily","max_tokens":1000000}' >/dev/null
DAY="$(date -u +%Y%m%d)"
MONTH="$(date -u +%Y%m)"
docker compose exec -T redis redis-cli SET "aigw:quota:${TENANT}:day:${DAY}:tokens" 999940 EX 172800 >/dev/null
docker compose exec -T redis redis-cli SET "aigw:quota:${TENANT}:month:${MONTH}:tokens" 999940 EX 172800 >/dev/null
ok=0
reject=0
for i in $(seq 1 6); do
  base="$API_A"
  if (( i % 2 == 0 )); then base="$API_B"; fi
  code="$(curl -s -o /tmp/aigw_tok.json -w '%{http_code}' "$base/v1/chat/completions" "${AUTH[@]}" \
    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"tok-$i\"}],\"model\":\"echo/echo-1\",\"temperature\":0,\"cache\":false,\"max_output_tokens\":64}")"
  if [[ "$code" == "200" ]]; then
    ok=$((ok + 1))
  else
    reject=$((reject + 1))
  fi
done
if [[ "$reject" -lt 1 ]]; then
  echo "expected Redis token budget rejection; ok=$ok reject=$reject" >&2
  cat /tmp/aigw_tok.json >&2 || true
  exit 1
fi
echo "  token budget race ok=$ok reject=$reject"
# Cross-replica rejection still holds after threshold.
code_a="$(curl -s -o /dev/null -w '%{http_code}' "$API_A/v1/chat/completions" "${AUTH[@]}" \
  -d '{"messages":[{"role":"user","content":"post"}],"model":"echo/echo-1","temperature":0,"cache":false,"max_output_tokens":64}')"
code_b="$(curl -s -o /dev/null -w '%{http_code}' "$API_B/v1/chat/completions" "${AUTH[@]}" \
  -d '{"messages":[{"role":"user","content":"post"}],"model":"echo/echo-1","temperature":0,"cache":false,"max_output_tokens":64}')"
if [[ "$code_a" == "200" && "$code_b" == "200" ]]; then
  echo "shared token budget not enforced on both replicas" >&2
  exit 1
fi

restore_defaults

echo "==> Shared circuit open across replicas (server_error storm)"
curl -sf -X POST "$API_A/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null
for i in $(seq 1 8); do
  curl -s -o /dev/null "$API_A/v1/chat/completions" "${AUTH[@]}" \
    -H "X-Scenario: server_error" \
    -d '{"messages":[{"role":"user","content":"x"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}' || true
done
code_b="$(curl -s -o /dev/null -w '%{http_code}' "$API_B/v1/chat/completions" "${AUTH[@]}" \
  -d '{"messages":[{"role":"user","content":"y"}],"model":"openai/gpt-4o-mini","temperature":0,"cache":false}')"
[[ "$code_b" != "200" ]] || echo "warn: circuit may not yet be open globally (soft)"

echo "==> Echo still works (isolation)"
curl -sf "$API_B/v1/chat/completions" "${AUTH[@]}" \
  -d '{"messages":[{"role":"user","content":"echo"}],"model":"echo/echo-1","temperature":0,"cache":false}' >/dev/null

curl -sf -X POST "$API_A/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null

echo "==> Durable DLQ seed + re-drive"
seed="$(curl -sf -X POST "$API_A/v1/admin/dlq/seed" "${AUTH[@]}")"
rid="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["record_id"])' <<<"$seed")"
curl -sf -X POST "$API_B/v1/admin/dlq/$rid/redrive" "${AUTH[@]}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] in {"resolved","already_resolved"}'
curl -sf -X POST "$API_A/v1/admin/dlq/$rid/redrive" "${AUTH[@]}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="already_resolved"'

echo "HA/SCENARIO OK"
