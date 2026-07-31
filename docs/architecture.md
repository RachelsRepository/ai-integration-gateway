# Architecture

## Overview

The gateway follows strict Clean Architecture. Dependencies point inward: delivery
mechanisms and adapters depend on application ports; the domain depends on nothing.

```text
┌────────────────────────────────────────────────────────────┐
│ API (FastAPI) / Workers                                    │
├────────────────────────────────────────────────────────────┤
│ Application (use cases, ports, application services)       │
├────────────────────────────────────────────────────────────┤
│ Domain (entities, value objects, policies, domain services)│
└────────────────────────────────────────────────────────────┘
          ▲
          │ implements
┌─────────┴──────────────────────────────────────────────────┐
│ Infrastructure (providers, SQL/Redis/Kafka, security, …)   │
└────────────────────────────────────────────────────────────┘
```

Boundaries are enforced by import-linter contracts in `pyproject.toml` and by the
architecture test suite.

## Core flows

1. Authenticate the caller (API key or JWT) and resolve the tenant.
2. Enforce RBAC, rate limits and quotas.
3. Apply guardrails (injection screening, PII redaction).
4. Route to a model using tenant policy, cost, latency and health.
5. Execute through the resilient provider executor (retries, circuit breaker, failover).
6. Filter output, persist conversation/usage/audit, stage domain events in the outbox.
7. Background workers aggregate usage, publish outbox events and process the DLQ.

## Provider abstraction

Every vendor implements `LLMProvider`. Business logic never imports vendor SDKs. The
Echo provider is always available for local development and contract tests.

## Persistence

- Alembic migrations define the PostgreSQL schema for tenants, credentials, conversations,
  prompts, agent runs, usage, audit events and the transactional outbox.
- The default composition root wires an in-memory unit of work that implements the same
  repository ports (used for local development and the automated test suite). Production
  deployments should replace that factory with a SQLAlchemy-backed unit of work against
  the migrated schema.
- Redis adapters provide response/embedding caches, rate-limit counters and distributed
  locks; local mode may substitute the in-memory cache.

## Events

Domain events are written to the outbox in the same transaction as business writes. A
background relay publishes unpublished rows and moves exhausted failures to a dead-letter
queue. A Kafka publisher adapter is provided; the default local composition uses an
in-memory publisher so the stack runs without a broker.
