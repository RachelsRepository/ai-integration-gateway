# Security guide

## Authentication

- API keys are stored as HMAC-SHA256 digests with a server-side pepper. Only a non-secret
  prefix is retained for lookup.
- JWTs are validated against a JWKS endpoint or shared secret, with issuer, audience and
  expiry checks.
- Health endpoints may be anonymous; all `/v1/*` routes require credentials.

## Authorisation

Roles (`admin`, `operator`, `developer`, `service`, `read_only`) map to fine-grained
permissions. Use cases call `principal.require(Permission.…)` before mutating state.

## Tenant isolation

Repositories accept `tenant_id` as a query constraint. Conversation, prompt and agent-run
lookups never cross tenant boundaries.

## Request security

- Optional HMAC request signing (`X-Signature`, `X-Signature-Timestamp`)
- Configurable maximum request body size
- Prompt-injection heuristics on untrusted content
- PII redaction before provider egress
- Output filtering for credential-shaped completions
- Structured audit events without prompt/completion bodies

## Secrets

Credentials are referenced, not inlined, in configuration. The secret resolver supports
environment variables and files; production deployments should inject values from a
secrets manager into those references.

Production-like environments (`staging`, `production`) reject:

- `debug=true`
- OpenAPI docs enabled
- `literal://` API-key pepper references and placeholder `change-me` values
- JWT enabled without JWKS or a shared-secret reference
- Wildcard CORS origins or trusted hosts
