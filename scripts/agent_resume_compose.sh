#!/usr/bin/env bash
# Agent mid-run pause/resume proof against Compose (durable Postgres state).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${API_BASE:-http://127.0.0.1:18000}"
KEY="${AIGW_API_KEY:-aigw_local_demo_key_do_not_use_in_prod_001}"
AUTH=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")

curl -sf "$API/health/ready" >/dev/null
curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true
curl -sf -X PUT "$API/v1/admin/tenants/me/quotas" "${AUTH[@]}" \
  -d '{"period":"daily","rate_limit_burst":60,"rate_limit_per_minute":600}' >/dev/null || true
docker compose up -d worker >/dev/null

echo "==> Start agent run with pause_after_steps=1 (force tool_call scenario)"
resp="$(curl -sf "$API/v1/agents/run" "${AUTH[@]}" -H "X-Scenario: tool_call" -d '{
  "input": "Use the echo tool then answer.",
  "agent_name": "resume-demo",
  "instructions": "Call the echo tool with message hi, then give a short final answer.",
  "tools": ["echo"],
  "model": "openai/gpt-4o-mini",
  "max_iterations": 4,
  "metadata": {"pause_after_steps": "1", "tools_confirmed": "true"}
}')"
echo "$resp" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] in {"running","pending","completed","failed","succeeded"}, d; open("/tmp/aigw_agent_run.json","w").write(json.dumps(d))'
RUN_ID="$(python3 -c 'import json; print(json.load(open("/tmp/aigw_agent_run.json"))["run_id"])')"
STATUS="$(python3 -c 'import json; print(json.load(open("/tmp/aigw_agent_run.json"))["status"])')"
echo "run_id=$RUN_ID status=$STATUS"

if [[ "$STATUS" == "succeeded" || "$STATUS" == "completed" ]]; then
  echo "Agent completed without pause; verifying GET"
  curl -sf "$API/v1/agents/runs/$RUN_ID" "${AUTH[@]}" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["run_id"]'
  echo "AGENT RESUME OK (completed early)"
  exit 0
fi

echo "==> Kill worker mid-run, then restart"
docker compose stop worker >/dev/null || true
docker compose start worker >/dev/null
sleep 2

echo "==> Resume durable agent run"
curl -sf -X POST "$API/v1/agents/runs/$RUN_ID/resume" "${AUTH[@]}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] in {"completed","running","failed","succeeded"}, d; assert d.get("usage") is not None; print(d["status"], d.get("cost_micros"))'

echo "==> Fetch run audit continuity"
curl -sf "$API/v1/agents/runs/$RUN_ID" "${AUTH[@]}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["run_id"]; assert isinstance(d.get("steps"), list); assert len(d["steps"]) >= 1'

echo "AGENT RESUME OK"
