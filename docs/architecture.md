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

- PostgreSQL holds tenants, credentials, conversations, prompts, agent runs, usage,
  audit events and the transactional outbox.
- Redis holds response/embedding caches, rate-limit counters and distributed locks.
- An in-memory unit of work mirrors the same repository ports for fast tests.

## Events

Domain events are written to the outbox in the same database transaction as business
writes, then relayed to Kafka by a worker. This preserves at-least-once delivery without
dual-write races.
