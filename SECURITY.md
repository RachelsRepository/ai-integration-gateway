# Security Policy

This repository is a verified reference implementation of a multi-provider AI
integration gateway. It is not a certified production service.

## Reporting

Please do not file public issues for suspected vulnerabilities that could expose
tenants or credentials. Prefer a private report to the repository maintainers.

## Scope

Security-relevant controls in this codebase include:

- API key and optional JWT authentication
- tenant isolation and RBAC
- production fail-closed configuration validation
- quota/budget reservations
- constrained allowlisted tool execution (not a hardened code sandbox)
- PII redaction and injection screening helpers

These controls require independent security and privacy review before production use.

See also [docs/security.md](docs/security.md).
