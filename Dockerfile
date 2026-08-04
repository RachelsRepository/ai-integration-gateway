# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# Test image: install locked optional test/dev deps at build time into the venv.
# Runtime containers never pip-install into /opt/venv.
FROM builder AS test

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src

RUN /opt/venv/bin/pip install ".[dev]"

RUN groupadd --system gateway \
    && useradd --system --gid gateway --home /app --shell /usr/sbin/nologin gateway \
    && mkdir -p /app \
    && chown -R gateway:gateway /app

WORKDIR /app
USER gateway

# Default command for compose `integration` service; CI mounts live sources under /app.
CMD ["pytest", "tests/integration", "-q", "--tb=short"]

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    AIGW_HOST=0.0.0.0 \
    AIGW_PORT=8000

RUN groupadd --system gateway \
    && useradd --system --gid gateway --home /app --shell /usr/sbin/nologin gateway

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/migrations ./migrations
COPY --from=builder /build/alembic.ini ./alembic.ini
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && chown -R gateway:gateway /app

USER gateway

EXPOSE 8000

# Health checks belong in Compose/orchestrator service definitions. This image serves
# api, worker, and migrate roles, so a single image HEALTHCHECK would be incorrect.

ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
