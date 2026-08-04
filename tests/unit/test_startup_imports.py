"""Focused module-compilation / factory boot smoke tests."""

from __future__ import annotations

import importlib

import pytest

from ai_gateway.api.app import create_app
from ai_gateway.config.settings import Settings
from ai_gateway.container import build_container


@pytest.mark.parametrize(
    "module_name",
    [
        "ai_gateway.api.app",
        "ai_gateway.container",
        "ai_gateway.workers.main",
        "ai_gateway.infrastructure.persistence.sqlalchemy",
        "ai_gateway.infrastructure.providers.factory",
        "ai_gateway.infrastructure.events.kafka_publisher",
    ],
)
def test_critical_modules_import(module_name: str) -> None:
    """Critical runtime modules must import without side-effect failures."""
    importlib.import_module(module_name)


@pytest.mark.asyncio
async def test_memory_container_builds() -> None:
    """Local memory-mode composition must wire without unresolved dependencies."""
    settings = Settings(
        environment="local",
        persistence_backend="memory",
        auth={"jwt_enabled": False, "api_keys_enabled": True},
        kafka={"enabled": False},
        providers={"enabled": ("echo",)},
        docs_enabled=True,
    )
    container = await build_container(settings)
    try:
        assert "echo" in {p.value for p in container.services.providers.configured()}
        app = create_app(settings)
        assert app.title
    finally:
        await container.aclose()
