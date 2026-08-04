"""API-level tests against the FastAPI app."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from ai_gateway.api.app import create_app
from ai_gateway.config.settings import Settings
from ai_gateway.domain.entities.tenant import Role, Tenant
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.infrastructure.persistence.memory import InMemoryUnitOfWork
from ai_gateway.infrastructure.security.api_keys import ApiKeyHasher


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    InMemoryUnitOfWork.reset()
    settings = Settings(
        environment="local",
        auth={"jwt_enabled": False, "api_key_pepper_ref": "literal://test-pepper"},
        providers={"enabled": ("echo",)},
        observability={"metrics_enabled": False, "log_format": "console"},
        kafka={"enabled": False},
        docs_enabled=True,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        hasher = ApiKeyHasher("test-pepper")
        tenant = Tenant(name="api", id=TenantId("22222222-2222-4222-8222-222222222222"))
        plaintext, key = hasher.mint(tenant_id=tenant.id, roles=frozenset({Role.ADMIN}))
        async with app.state.container.services.uow_factory() as uow:
            await uow.tenants.upsert(tenant)
            await uow.api_keys.add(key)
            await uow.commit()
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.headers["X-API-Key"] = plaintext
            yield ac


@pytest.mark.asyncio
async def test_health_and_models(client: AsyncClient) -> None:
    live = await client.get("/health/live")
    assert live.status_code == 200
    models = await client.get("/v1/models")
    assert models.status_code == 200
    assert any(item["id"].startswith("echo/") for item in models.json())


@pytest.mark.asyncio
async def test_chat_completions(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "hello api"}],
            "model": "echo/echo-1",
            "temperature": 0,
            "cache": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "hello api" in body["choices"][0]["message"]["content"]


@pytest.mark.asyncio
async def test_embeddings_and_prompts(client: AsyncClient) -> None:
    emb = await client.post(
        "/v1/embeddings",
        json={"input": ["a", "b"], "model": "echo/echo-embed"},
    )
    assert emb.status_code == 200
    assert len(emb.json()["data"]) == 2

    prompt = await client.post(
        "/v1/prompts",
        json={"name": "api-prompt", "template": "Say hi to {{ name }}"},
    )
    assert prompt.status_code == 200
    listed = await client.get("/v1/prompts")
    assert listed.status_code == 200
    assert any(item["name"] == "api-prompt" for item in listed.json())


@pytest.mark.asyncio
async def test_unauthenticated_rejected() -> None:
    settings = Settings(
        environment="local",
        auth={"jwt_enabled": False, "api_key_pepper_ref": "literal://test-pepper"},
        providers={"enabled": ("echo",)},
        observability={"metrics_enabled": False, "log_format": "console"},
        kafka={"enabled": False},
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/v1/models")
            assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_dlq_seed_redrive_and_quotas(client: AsyncClient) -> None:
    quotas = await client.put(
        "/v1/admin/tenants/me/quotas",
        json={"max_requests": 50, "period": "daily"},
    )
    assert quotas.status_code == 200
    assert quotas.json()["quotas"]["daily"]["max_requests"] == 50

    circuits = await client.post("/v1/admin/circuits/reset")
    assert circuits.status_code == 200
    assert circuits.json()["status"] in {"ok", "skipped"}

    seed = await client.post("/v1/admin/dlq/seed")
    assert seed.status_code == 200
    record_id = seed.json()["record_id"]

    listed = await client.get("/v1/admin/dlq")
    assert listed.status_code == 200
    assert listed.json()["size"] >= 1

    redrive = await client.post(f"/v1/admin/dlq/{record_id}/redrive")
    assert redrive.status_code == 200
    assert redrive.json()["status"] in {"resolved", "already_resolved"}

    again = await client.post(f"/v1/admin/dlq/{record_id}/redrive")
    assert again.status_code == 200
    assert again.json()["status"] == "already_resolved"


@pytest.mark.asyncio
async def test_scenario_header_ignored_without_forwarding(client: AsyncClient) -> None:
    # Default test settings leave forwarding off; request still succeeds via echo.
    response = await client.post(
        "/v1/chat/completions",
        headers={"X-Scenario": "server_error"},
        json={
            "messages": [{"role": "user", "content": "ignore-scenario"}],
            "model": "echo/echo-1",
            "temperature": 0,
            "cache": False,
        },
    )
    assert response.status_code == 200
