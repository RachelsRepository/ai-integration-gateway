.PHONY: help install lint format typecheck test coverage architecture openapi run worker migrate docker-up docker-down terraform-fmt terraform-validate verify

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy
LINT_IMPORTS ?= .venv/bin/lint-imports

help:
	@echo "Targets: install lint format typecheck test coverage architecture openapi run worker migrate docker-up docker-down verify"

install:
	python3.11 -m venv .venv
	$(PIP) install -U pip wheel
	$(PIP) install -e ".[dev]"
	.venv/bin/pre-commit install || true

lint:
	$(RUFF) check src tests migrations

format:
	$(RUFF) format src tests migrations
	$(RUFF) check --fix src tests migrations

typecheck:
	PYTHONPATH=src $(MYPY) src tests

test:
	PYTHONPATH=src $(PYTEST) -q

coverage:
	PYTHONPATH=src $(PYTEST) --cov=ai_gateway --cov-report=term-missing --cov-report=xml -q

architecture:
	PYTHONPATH=src $(LINT_IMPORTS)

openapi:
	PYTHONPATH=src $(PYTHON) -m ai_gateway.cli openapi --output openapi.json

run:
	PYTHONPATH=src AIGW_ENVIRONMENT=local AIGW_AUTH_JWT_ENABLED=false AIGW_KAFKA_ENABLED=false AIGW_PROVIDER_ENABLED='["echo"]' AIGW_OTEL_LOG_FORMAT=console $(PYTHON) -m ai_gateway

worker:
	PYTHONPATH=src AIGW_ENVIRONMENT=local AIGW_KAFKA_ENABLED=false AIGW_PROVIDER_ENABLED='["echo"]' $(PYTHON) -m ai_gateway.workers.main

migrate:
	PYTHONPATH=src $(PYTHON) -m alembic upgrade head

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down -v

terraform-fmt:
	cd deploy/terraform && terraform fmt -check -recursive

terraform-validate:
	cd deploy/terraform && terraform init -backend=false && terraform validate

verify: lint typecheck architecture coverage openapi
	@echo "Verification complete"
