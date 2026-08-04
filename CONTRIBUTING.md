# Contributing

Thank you for contributing to this reference implementation.

## Development

1. Use Python 3.11+.
2. Create a virtualenv and install `.[dev]`.
3. Copy `.env.example` to `.env`.
4. Run `make verify` before opening a pull request.

## Runtime verification

Docker Compose is required for end-to-end, HA, chaos, and load scripts:

```bash
make verify-runtime
```

Do not commit secrets, `.env` files, coverage artifacts, or Finder duplicates.

More detail: [docs/contribution.md](docs/contribution.md) and
[docs/developer-guide.md](docs/developer-guide.md).
