#!/usr/bin/env bash
# Query durable accounting invariants after a Compose runtime suite.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

docker compose exec -T postgres psql -U gateway -d gateway -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'tenants' AS metric, count(*)::text AS value FROM tenants
UNION ALL SELECT 'api_keys', count(*)::text FROM api_keys
UNION ALL SELECT 'usage_records', count(*)::text FROM usage_records
UNION ALL SELECT 'agent_runs', count(*)::text FROM agent_runs
UNION ALL SELECT 'agent_steps', count(*)::text FROM agent_steps
UNION ALL SELECT 'dlq_records', count(*)::text FROM dead_letter_records
UNION ALL SELECT 'outbox_pending', count(*)::text FROM outbox_events WHERE published_at IS NULL
UNION ALL SELECT 'outbox_published', count(*)::text FROM outbox_events WHERE published_at IS NOT NULL
UNION ALL SELECT 'stuck_running_agents', count(*)::text FROM agent_runs WHERE status = 'running'
UNION ALL SELECT 'duplicate_usage_request_ids', coalesce((
  SELECT count(*)::text FROM (
    SELECT request_id FROM usage_records GROUP BY request_id, operation HAVING count(*) > 1
  ) d
), '0');
SQL

echo "==> Redis circuit keys"
docker compose exec -T redis redis-cli --scan --pattern 'aigw:cb:*' | head -20 || true
echo "==> Redis quota inflight keys"
docker compose exec -T redis redis-cli --scan --pattern 'aigw:quota:*:inflight' || true
