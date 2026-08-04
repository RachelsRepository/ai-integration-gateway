.PHONY: help install lint format typecheck test coverage architecture openapi run worker migrate docker-up docker-down terraform-fmt terraform-validate verify verify-runtime

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy
LINT_IMPORTS ?= .venv/bin/lint-imports

help:
	@echo "Targets: install lint format typecheck test coverage architecture openapi run worker migrate docker-up docker-down verify verify-runtime"

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
	docker compose --profile ha up --build -d

docker-down:
	docker compose --profile ha down -v --remove-orphans

terraform-fmt:
	cd deploy/terraform && terraform fmt -check -recursive

terraform-validate:
	cd deploy/terraform && terraform init -backend=false && terraform validate

verify: lint typecheck architecture coverage openapi
	@echo "Verification complete"

verify-runtime:
	chmod +x scripts/*.sh
	docker compose --profile ha up --build -d
	REQUIRE_HA=1 AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001 \
		API_BASE=http://127.0.0.1:18000 API_BASE_B=http://127.0.0.1:18003 \
		scripts/wait_for_stack.sh
	AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001 API_BASE=http://127.0.0.1:18000 \
		scripts/e2e_compose.sh
	AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001 API_BASE=http://127.0.0.1:18000 \
		scripts/recreate_embeddings_compose.sh
	AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001 \
		API_BASE_A=http://127.0.0.1:18000 API_BASE_B=http://127.0.0.1:18003 \
		scripts/ha_quota_compose.sh
	AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001 API_BASE=http://127.0.0.1:18000 \
		scripts/provider_matrix_compose.sh
	AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001 API_BASE=http://127.0.0.1:18000 \
		scripts/agent_resume_compose.sh
	AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001 API_BASE=http://127.0.0.1:18000 \
		scripts/chaos_compose.sh
	AIGW_API_KEY=aigw_local_demo_key_do_not_use_in_prod_001 API_BASE=http://127.0.0.1:18000 \
		LOAD_CONCURRENCY=4 LOAD_REQUESTS=20 scripts/load_compose.sh
	@echo "Runtime verification complete"
