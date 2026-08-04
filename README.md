# AI Integration Gateway

Multi-provider AI integration gateway with production-inspired controls: unified API,
tenant isolation, routing, streaming, metering, retries/circuits, and durable usage
accounting. This is a **verified reference implementation** and **production-oriented
architecture**. It is **not** a certified production service and still requires
independent security, privacy, legal, and operational review before production use.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python | 3.11 or 3.12 |
| Package manager | `pip` (venv recommended) |
| Docker | Engine 24+ with Compose v2 (`docker compose`) |
| Resources | ~4 CPU / 8 GB RAM recommended for the HA Compose profile |
| Startup | First HA build typically 2–5 minutes; later starts usually under 2 minutes |
| Host ports | API `18000`, api-2 `18003`, provider-a `18001`, provider-b `18002` |

## Recommended quick start (Compose)

```bash
cp .env.example .env
docker compose --profile ha up --build -d
export AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001
REQUIRE_HA=1 API_BASE=http://127.0.0.1:18000 API_BASE_B=http://127.0.0.1:18003 \
  ./scripts/wait_for_stack.sh
curl -s http://127.0.0.1:18000/health/ready
```

Cleanup:

```bash
docker compose --profile ha down -v --remove-orphans
```

## Local process quick start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
make run
```

```bash
curl -s http://127.0.0.1:8000/health/live
```

On first local boot the gateway seeds a demo tenant and prints a
`LOCAL DEMO API KEY`. Compose pins a stable local key via `AIGW_DEMO_API_KEY`.

## Capabilities

- Multi-provider adapters: OpenAI, Anthropic, Google Gemini, Azure OpenAI, AWS Bedrock, plus Echo and fictional Compose providers A/B
- Clean Architecture with dependency inversion and import-linter contracts
- FastAPI: chat completions, embeddings, agents, prompts, models, providers, health/ready, metrics
- API key and optional JWT authentication with RBAC and tenant isolation
- Cost/latency/capability-aware routing with failover
- SSE streaming with cancellation and timeouts
- Durable agent orchestration with mid-run resume and a **bounded** tool runner (not a hardened sandbox)
- Redis-backed distributed circuit breakers shared across API/worker replicas
- Shared Redis quota/budget reservations with post-request settlement
- Durable PostgreSQL DLQ with authenticated local re-drive
- Kafka domain events via transactional outbox (when enabled)
- PostgreSQL persistence and Redis caching/rate limiting in Compose
- OpenTelemetry, structured logging, Prometheus metrics, correlation IDs
- Usage metering, cost tracking, daily/monthly quotas
- Docker Compose HA profile (two API replicas), Terraform scaffolding, quality + runtime CI jobs

See [docs/provider-capabilities.md](docs/provider-capabilities.md) and
[docs/tool-execution.md](docs/tool-execution.md).

Local/test-only `X-Scenario` forwarding is enabled in Compose via
`AIGW_PROVIDER_SCENARIO_FORWARDING=true` and is rejected in production settings.

## Verified commands

```bash
# Quality
make verify                 # lint, typecheck, architecture, coverage, openapi
make test                   # unit/integration pytest -q
make coverage               # coverage with 85% fail-under

# Runtime (requires Docker Compose)
make docker-up
REQUIRE_HA=1 API_BASE=http://127.0.0.1:18000 API_BASE_B=http://127.0.0.1:18003 \
  ./scripts/wait_for_stack.sh
export AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001
API_BASE=http://127.0.0.1:18000 ./scripts/e2e_compose.sh
API_BASE=http://127.0.0.1:18000 ./scripts/recreate_embeddings_compose.sh
API_BASE_A=http://127.0.0.1:18000 API_BASE_B=http://127.0.0.1:18003 ./scripts/ha_quota_compose.sh
API_BASE=http://127.0.0.1:18000 ./scripts/provider_matrix_compose.sh
API_BASE=http://127.0.0.1:18000 ./scripts/agent_resume_compose.sh
API_BASE=http://127.0.0.1:18000 ./scripts/chaos_compose.sh
API_BASE=http://127.0.0.1:18000 ./scripts/load_compose.sh
# or: make verify-runtime
# three clean cycles: ./scripts/clean_cycles_compose.sh
make docker-down
```

Load numbers in [docs/load-results.md](docs/load-results.md) are **local bounded load
verification**, not production capacity planning.

## Quality gates

```bash
make verify
```

GitHub Actions runs quality checks plus a **Runtime Compose** job (wait helper, e2e,
embeddings recreate regression, HA quotas, provider matrix, agent resume, chaos, load,
and a second clean volume cycle).

## Project layout

```text
src/ai_gateway/
  domain/           # entities, value objects, policies, pure services
  application/      # ports, DTOs, use cases, application services
  infrastructure/   # adapters: providers, persistence, cache, security, events
  api/              # FastAPI delivery
  workers/          # background jobs
  observability/    # logging, metrics, tracing, correlation
  config/           # typed settings
```

## Documentation

- [Architecture](docs/architecture.md)
- [Provider capabilities](docs/provider-capabilities.md)
- [Tool execution](docs/tool-execution.md)
- [Load results](docs/load-results.md)
- [Sequence diagrams](docs/sequence-diagrams.md)
- [API guide](docs/api.md)
- [Deployment](docs/deployment.md)
- [Security](docs/security.md)
- [Developer guide](docs/developer-guide.md)
- [Contribution guide](docs/contribution.md)
- [ADRs](docs/adr/)
- [SECURITY.md](SECURITY.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## License

See [LICENSE](LICENSE).
