# ADR 0002: Transactional outbox for domain events

## Status

Accepted

## Context

Publishing to Kafka in the request path creates a dual-write problem: the database may
commit while the broker publish fails, or vice versa.

## Decision

Persist domain events to an `outbox_events` table in the same transaction as business
writes. A background relay publishes unpublished rows and moves exhausted failures to a
dead-letter queue.

## Consequences

- At-least-once delivery; consumers must be idempotent
- Request path latency is independent of broker availability
- Additional worker operational surface
