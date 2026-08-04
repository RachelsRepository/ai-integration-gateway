#!/usr/bin/env bash
# Formal load-test smoke against Compose fictional providers. Records local numbers only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${API_BASE:-http://127.0.0.1:18000}"
CONCURRENCY="${LOAD_CONCURRENCY:-8}"
REQUESTS="${LOAD_REQUESTS:-40}"
OUT="${LOAD_REPORT:-docs/load-results.md}"
KEY="${AIGW_API_KEY:-aigw_local_demo_key_do_not_use_in_prod_001}"
AUTH=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")

curl -sf -X POST "$API/v1/admin/circuits/reset" "${AUTH[@]}" >/dev/null || true

export API_BASE="$API"
export AIGW_API_KEY="$KEY"
export LOAD_CONCURRENCY="$CONCURRENCY"
export LOAD_REQUESTS="$REQUESTS"
export LOAD_REPORT="$OUT"

python3 - <<'PY'
import concurrent.futures
import json
import os
import statistics
import time
import urllib.request

api = os.environ["API_BASE"]
key = os.environ["AIGW_API_KEY"]
concurrency = int(os.environ["LOAD_CONCURRENCY"])
requests_n = int(os.environ["LOAD_REQUESTS"])
out = os.environ["LOAD_REPORT"]

def one(i: int) -> tuple[float, int]:
    model = "echo/echo-1" if i % 5 == 0 else "openai/gpt-4o-mini"
    body = json.dumps({
        "messages": [{"role": "user", "content": f"load-{i}"}],
        "model": model,
        "temperature": 0,
        "cache": False,
    }).encode()
    req = urllib.request.Request(
        f"{api}/v1/chat/completions",
        data=body,
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            code = resp.status
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", 599) or 599
    return (time.perf_counter() - started) * 1000, int(code)

latencies = []
codes = []
started = time.perf_counter()
with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
    for latency_ms, code in pool.map(one, range(requests_n)):
        latencies.append(latency_ms)
        codes.append(code)
elapsed = time.perf_counter() - started
latencies.sort()
def pct(p: float) -> float:
    if not latencies:
        return 0.0
    idx = min(len(latencies) - 1, int(round((p / 100) * (len(latencies) - 1))))
    return latencies[idx]
ok = sum(1 for c in codes if 200 <= c < 300)
err = len(codes) - ok
lines = [
    "# Load-test results (local Compose smoke)",
    "",
    "These numbers are **local smoke measurements** against fictional providers.",
    "They do not represent production capacity.",
    "",
    f"- Environment: Docker Compose on developer workstation",
    f"- API replicas: 1+ (host :18000)",
    f"- Concurrency: {concurrency}",
    f"- Requests: {requests_n}",
    f"- Duration: {elapsed:.2f}s",
    f"- Throughput: {requests_n / elapsed:.2f} req/s",
    f"- Success: {ok}",
    f"- Errors: {err}",
    f"- Error rate: {err / max(requests_n, 1):.2%}",
    f"- p50 latency: {pct(50):.1f} ms",
    f"- p95 latency: {pct(95):.1f} ms",
    f"- p99 latency: {pct(99):.1f} ms",
    f"- mean latency: {statistics.mean(latencies) if latencies else 0:.1f} ms",
    "",
]
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n".join(lines))
if ok == 0:
    raise SystemExit(1)
PY

echo "LOAD OK -> $OUT"
