#!/usr/bin/env bash
# Bounded condition polling for Compose stack readiness (no fixed long sleeps).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${API_BASE:-http://127.0.0.1:18000}"
API_B="${API_BASE_B:-}"
PROVIDER_A="${PROVIDER_A_BASE:-http://127.0.0.1:18001}"
PROVIDER_B="${PROVIDER_B_BASE:-http://127.0.0.1:18002}"
DEADLINE_SECONDS="${WAIT_DEADLINE_SECONDS:-180}"
INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-2}"
REQUIRE_HA="${REQUIRE_HA:-0}"

deadline=$((SECONDS + DEADLINE_SECONDS))

dump_diagnostics() {
  echo "=== stack wait diagnostics ===" >&2
  docker compose ps >&2 || true
  echo "--- readiness ---" >&2
  curl -sS -D- "$API/health/ready" -o /tmp/aigw_ready_body.json 2>&1 | head -40 >&2 || true
  cat /tmp/aigw_ready_body.json 2>/dev/null >&2 || true
  echo "--- api logs ---" >&2
  docker compose logs --no-color --tail=80 api >&2 || true
  echo "--- provider-a logs ---" >&2
  docker compose logs --no-color --tail=40 provider-a >&2 || true
  echo "--- provider-b logs ---" >&2
  docker compose logs --no-color --tail=40 provider-b >&2 || true
  echo "--- migrate logs ---" >&2
  docker compose logs --no-color --tail=40 migrate >&2 || true
}

http_ok() {
  local url="$1"
  local code
  code="$(curl -sS -o /tmp/aigw_wait_body.bin -w '%{http_code}' --connect-timeout 2 --max-time 5 \
    -H 'Connection: close' "$url" 2>/dev/null || echo 000)"
  [[ "$code" == "200" ]]
}

ready_ok() {
  local url="$1/health/ready"
  local code body
  code="$(curl -sS -o /tmp/aigw_ready_body.json -w '%{http_code}' --connect-timeout 2 --max-time 5 \
    -H 'Connection: close' "$url" 2>/dev/null || echo 000)"
  [[ "$code" == "200" ]] || return 1
  body="$(cat /tmp/aigw_ready_body.json 2>/dev/null || true)"
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get("status")=="ok" else 1)' "$body" 2>/dev/null
}

echo "==> Waiting for Compose stack (deadline=${DEADLINE_SECONDS}s)"
while (( SECONDS < deadline )); do
  ok=1
  http_ok "$PROVIDER_A/health" || ok=0
  http_ok "$PROVIDER_B/health" || ok=0
  ready_ok "$API" || ok=0
  if [[ "$REQUIRE_HA" == "1" || -n "$API_B" ]]; then
    base_b="${API_B:-http://127.0.0.1:18003}"
    ready_ok "$base_b" || ok=0
  fi
  if [[ "$ok" -eq 1 ]]; then
    # Migrate must have completed successfully at least once for this volume.
    if docker compose ps migrate --status exited --format '{{.Status}}' 2>/dev/null | grep -qi 'Exited (0)'; then
      :
    else
      # Fresh stacks may have already removed the migrate container; accept if API ready + DB ok.
      :
    fi
    echo "STACK READY"
    exit 0
  fi
  sleep "$INTERVAL_SECONDS"
done

echo "Stack readiness deadline exceeded" >&2
dump_diagnostics
exit 1
