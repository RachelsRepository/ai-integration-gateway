# AI Integration Gateway

Production-style AI integration platform providing a unified, secure API across multiple
LLM providers. The gateway centralises provider abstraction, prompt routing, model
selection, streaming, agent execution, authentication, authorisation, rate limiting,
usage metering, cost tracking, retries, circuit breakers, observability, audit logging
and cloud-native deployment.

## Capabilities

- Multi-provider adapters: OpenAI, Anthropic, Google Gemini, Azure OpenAI, AWS Bedrock, plus a deterministic Echo provider for local development
- Clean Architecture with dependency inversion and import-linter contracts
- FastAPI surface: chat completions, embeddings, responses, agents, prompts, models, providers, health
- OAuth2/JWT and API key authentication with RBAC and tenant isolation
- Prompt templates with versioning, variable substitution and safety prompts
- Cost/latency/capability-aware routing with failover
- Server-Sent Events streaming with cancellation and timeouts
- Agent orchestration with tool registry and conversation memory
- Circuit breakers, retry with exponential backoff, dead-letter queue
- Kafka domain events via transactional outbox
- PostgreSQL persistence, Redis caching and rate limiting
- OpenTelemetry, structured logging, Prometheus metrics, correlation IDs
- Usage metering, cost tracking, daily/monthly quotas
- Docker, Docker Compose, Terraform and GitHub Actions

## Quick start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
make run
```

In another terminal:

```bash
curl -s http://127.0.0.1:8000/health/live
```

On first local boot the gateway seeds a demo tenant and prints a one-time
`LOCAL DEMO API KEY` to stdout. Export it before calling protected routes:

```bash
export AIGW_API_KEY='…paste key from server logs…'
```

Example chat request:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "X-API-Key: $AIGW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "hello"}],
    "model": "echo/echo-1",
    "temperature": 0
  }'
```

## Docker Compose

```bash
docker compose up --build
```

Services: PostgreSQL, Redis, Kafka (KRaft), API, worker, and a one-shot migration.

## Quality gates

```bash
make verify
```

Runs Ruff, MyPy, import-linter, Pytest with coverage and OpenAPI export.

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
- [Sequence diagrams](docs/sequence-diagrams.md)
- [API guide](docs/api.md)
- [Deployment](docs/deployment.md)
- [Security](docs/security.md)
- [Developer guide](docs/developer-guide.md)
- [Contribution guide](docs/contribution.md)
- [ADRs](docs/adr/)

## License

See [LICENSE](LICENSE).
