#!/usr/bin/env bash
# Three consecutive clean-volume Compose cycles with full runtime suite.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export AIGW_API_KEY="${AIGW_API_KEY:-aigw_local_demo_key_do_not_use_in_prod_001}"
export API_BASE="${API_BASE:-http://127.0.0.1:18000}"
export API_BASE_A="${API_BASE_A:-http://127.0.0.1:18000}"
export API_BASE_B="${API_BASE_B:-http://127.0.0.1:18003}"
CYCLES="${CLEAN_CYCLES:-3}"

chmod +x scripts/*.sh

run_cycle() {
  local tag="$1"
  local started ended
  started="$(date +%s)"
  echo "===== CLEAN CYCLE $tag ====="
  docker compose --profile ha down -v --remove-orphans
  docker compose --profile ha up --build -d
  REQUIRE_HA=1 API_BASE="$API_BASE" API_BASE_B="$API_BASE_B" bash scripts/wait_for_stack.sh
  API_BASE="$API_BASE" bash scripts/e2e_compose.sh
  echo "${tag}_E2E:0"
  API_BASE="$API_BASE" bash scripts/recreate_embeddings_compose.sh
  echo "${tag}_RECREATE:0"
  API_BASE_A="$API_BASE_A" API_BASE_B="$API_BASE_B" bash scripts/ha_quota_compose.sh
  echo "${tag}_HA:0"
  API_BASE="$API_BASE" bash scripts/provider_matrix_compose.sh
  echo "${tag}_MATRIX:0"
  API_BASE="$API_BASE" bash scripts/agent_resume_compose.sh
  echo "${tag}_AGENT:0"
  API_BASE="$API_BASE" bash scripts/chaos_compose.sh
  echo "${tag}_CHAOS:0"
  API_BASE="$API_BASE" LOAD_CONCURRENCY=4 LOAD_REQUESTS=20 bash scripts/load_compose.sh
  echo "${tag}_LOAD:0"
  ended="$(date +%s)"
  echo "${tag}_DURATION_SECONDS:$((ended - started))"
  echo "${tag}_OK"
}

for i in $(seq 1 "$CYCLES"); do
  run_cycle "C$i"
done

docker compose --profile ha ps
echo "ALL_CLEAN_CYCLES_OK"
