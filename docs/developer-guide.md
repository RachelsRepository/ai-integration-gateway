# Developer guide

## Prerequisites

- Python 3.11+
- Docker and Docker Compose (optional, for full stack)
- Make

## Setup

```bash
make install
cp .env.example .env
make run
```

## Tests

```bash
make test
make coverage
make architecture
```

Tests use the in-memory unit of work and Echo provider by default. No external services
are required for the unit, architecture, security, streaming or contract suites.

## Adding a provider

1. Implement `LLMProvider` under `infrastructure/providers/`.
2. Map vendor errors onto `domain.errors.Provider*`.
3. Register models in `StaticModelCatalog`.
4. Wire construction in `build_providers`.
5. Add a contract test.

## Adding a use case

1. Define DTOs in `application/dto.py` if needed.
2. Implement the use case under `application/use_cases/`.
3. Depend only on ports and domain types.
4. Expose an HTTP route in `api/routes/`.
5. Cover with unit and API tests.

## Style

- Google-style docstrings on public APIs
- Full type hints; MyPy strict mode
- Ruff for linting and formatting
- No relative imports
