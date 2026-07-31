# ADR 0001: Clean Architecture

## Status

Accepted

## Context

The gateway must remain provider-agnostic and testable while growing to support many
enterprise concerns (auth, metering, routing, resilience). Framework and vendor coupling
in the core would make substitution expensive.

## Decision

Adopt Clean Architecture with four layers: domain, application, infrastructure and
delivery (API/workers). Application ports are Protocols. Import-linter enforces the
dependency rule in CI.

## Consequences

- Higher initial structure cost
- Domain and use cases are unit-testable without Docker
- Provider and persistence adapters can be replaced independently
