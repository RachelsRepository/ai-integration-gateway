# Load-test results (local bounded load verification)

These numbers are **local smoke measurements** against fictional providers on a
developer workstation. They are **not** production capacity planning numbers.

- Environment: Docker Compose on developer workstation (darwin)
- API replica count exercised: 1 host endpoint (`:18000`; HA profile also has `:18003`)
- Provider replica count: 1 each (provider-a, provider-b)
- Concurrency: 4
- Requests: 20
- Duration: 2.19s
- Throughput: 9.13 req/s
- Success: 20
- Errors: 0
- Error rate: 0.00%
- Retry rate: not separately instrumented in this smoke profile
- Fallback rate: not separately instrumented in this smoke profile
- Active streams: 0 (non-streaming mix with echo/openai chat)
- p50 latency: 280.9 ms
- p95 latency: 1045.5 ms
- p99 latency: 1050.0 ms
- mean latency: 432.8 ms
- First-token latency: n/a for this non-streaming profile
- Redis/DB/CPU/memory: not collected in this bounded smoke

Re-run:

```bash
export AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001
API_BASE=http://127.0.0.1:18000 LOAD_CONCURRENCY=4 LOAD_REQUESTS=20 ./scripts/load_compose.sh
```
