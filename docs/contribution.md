# Contribution guide

## Workflow

1. Create a feature branch from `main`.
2. Keep changes focused and covered by tests.
3. Run `make verify` before opening a pull request.
4. Ensure GitHub Actions is green.

## Commit messages

Prefer imperative subjects summarising the user-visible or architectural change, for
example:

```text
Add resilient provider failover for chat completions
```

## Pull requests

Include:

- Motivation and scope
- Test plan
- Any operational impact (migrations, config, dashboards)

## Code review expectations

- Domain purity preserved (no framework imports in `domain/`)
- Ports unchanged unless intentionally versioned
- Security-sensitive changes accompanied by security tests
- No secrets committed
