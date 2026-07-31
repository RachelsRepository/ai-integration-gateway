"""Tool registry and secret resolver tests."""

from __future__ import annotations

import pytest

from ai_gateway.application.ports.tools import ToolExecutionContext
from ai_gateway.domain.errors import ToolNotFoundError, ValidationError
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId
from ai_gateway.infrastructure.secrets.resolver import CompositeSecretResolver
from ai_gateway.infrastructure.tools.builtins import build_builtin_tools
from ai_gateway.infrastructure.tools.registry import InMemoryToolRegistry


@pytest.mark.asyncio
async def test_builtin_tools() -> None:
    registry = InMemoryToolRegistry(build_builtin_tools())
    ctx = ToolExecutionContext(tenant_id=TenantId("t"), request_id=RequestId("r"), agent_run_id="a")
    echo = await registry.get("echo").execute({"message": "hi"}, ctx)
    assert echo == "hi"
    calc = await registry.get("calculator").execute({"expression": "(2+3)*4"}, ctx)
    assert calc == "20.0"
    now = await registry.get("current_time").execute({}, ctx)
    assert "T" in now
    with pytest.raises(ToolNotFoundError):
        registry.get("missing")


@pytest.mark.asyncio
async def test_secret_resolver_env_and_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIGW_TEST_SECRET", "s3cr3t")
    resolver = CompositeSecretResolver(allow_literals=True)
    assert await resolver.resolve("env://AIGW_TEST_SECRET") == "s3cr3t"
    assert await resolver.resolve("literal://abc") == "abc"
    assert await resolver.resolve("plain") == "plain"
    with pytest.raises(ValidationError):
        await resolver.resolve("unknown://x")
