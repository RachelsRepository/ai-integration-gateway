#!/usr/bin/env bash
# Startup/recreate regression: embeddings must survive provider-A and API restarts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${API_BASE:-http://127.0.0.1:18000}"
KEY="${AIGW_API_KEY:-aigw_local_demo_key_do_not_use_in_prod_001}"
AUTH=(-H "X-API-Key: $KEY" -H "Content-Type: application/json" -H "Connection: close")
ROUNDS="${RECREATE_ROUNDS:-3}"

embeddings_ok() {
  local label="$1"
  local body code
  body="$(mktemp)"
  code="$(curl -sS -o "$body" -w '%{http_code}' --connect-timeout 5 --max-time 60 \
    "${AUTH[@]}" -d '{"model":"openai/text-embedding-3-small","input":["recreate-'"$label"'"]}' \
    "$API/v1/embeddings" || echo 000)"
  if [[ "$code" != "200" ]]; then
    echo "embeddings failed ($label) HTTP $code" >&2
    cat "$body" >&2 || true
    rm -f "$body"
    return 1
  fi
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); data=d.get("data") or d.get("embeddings") or []; assert len(data)>=1' "$body"
  rm -f "$body"
}

echo "==> Baseline stack wait"
API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
embeddings_ok baseline

for round in $(seq 1 "$ROUNDS"); do
  echo "==> Recreate round $round: stop provider-a"
  docker compose stop provider-a >/dev/null
  sleep 1
  # While down, embeddings to openai model must not succeed as a normal 200 completion.
  code="$(curl -sS -o /tmp/aigw_emb_down.json -w '%{http_code}' --connect-timeout 5 --max-time 30 \
    "${AUTH[@]}" -d '{"model":"openai/text-embedding-3-small","input":["down"]}' \
    "$API/v1/embeddings" || echo 000)"
  if [[ "$code" == "200" ]]; then
    echo "expected non-200 while provider-a stopped; got 200" >&2
    cat /tmp/aigw_emb_down.json >&2
    exit 1
  fi
  echo "  provider-a down -> HTTP $code"

  echo "==> Recreate round $round: start provider-a and wait"
  docker compose start provider-a >/dev/null
  API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
  # Clear any open circuit from the outage window.
  curl -sS -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true
  embeddings_ok "provider-a-up-$round"

  echo "==> Recreate round $round: restart API"
  docker compose restart api >/dev/null
  API_BASE="$API" bash "$ROOT/scripts/wait_for_stack.sh"
  embeddings_ok "api-restart-$round"
done

echo "RECREATE REGRESSION OK"
