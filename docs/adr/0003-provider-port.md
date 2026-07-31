# ADR 0003: Unified LLM provider port

## Status

Accepted

## Context

Vendors expose incompatible APIs for chat, tools, streaming and embeddings.

## Decision

Define a single `LLMProvider` Protocol with normalised request/response DTOs. Each vendor
is an adapter responsible for wire-format translation and error mapping. No vendor SDK
types cross the port boundary.

## Consequences

- Routing, metering and agents share one execution path
- New vendors require an adapter and catalogue entries only
- Some vendor-specific features are intentionally unsupported until modelled in the port
