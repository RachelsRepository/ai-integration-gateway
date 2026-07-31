"""Shared pytest fixtures."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("AIGW_ENVIRONMENT", "local")
os.environ.setdefault("AIGW_AUTH_JWT_ENABLED", "false")
os.environ.setdefault("AIGW_AUTH_API_KEY_PEPPER_REF", "literal://test-pepper")
os.environ.setdefault("AIGW_PROVIDER_ENABLED", '["echo"]')
os.environ.setdefault("AIGW_OTEL_LOG_FORMAT", "console")
os.environ.setdefault("AIGW_OTEL_METRICS_ENABLED", "false")
os.environ.setdefault("AIGW_KAFKA_ENABLED", "false")

from ai_gateway.application.dto import RequestContext
from ai_gateway.application.services.audit import AuditTrail
from ai_gateway.application.services.caching import EmbeddingCache, ResponseCache
from ai_gateway.application.services.execution import ProviderExecutor
from ai_gateway.application.services.guardrails import GuardrailService
from ai_gateway.application.services.metering import UsageMeter
from ai_gateway.application.services.router import ModelRouter
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.config.settings import get_settings
from ai_gateway.domain.entities.tenant import Principal, Role, Tenant
from ai_gateway.domain.policies.retry import RetryPolicy
from ai_gateway.domain.services.cost import CostCalculator
from ai_gateway.domain.services.token_estimation import TokenEstimator
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.infrastructure.cache.memory import InMemoryCache
from ai_gateway.infrastructure.clock import SystemClock
from ai_gateway.infrastructure.persistence.memory import InMemoryUnitOfWork
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog
from ai_gateway.infrastructure.providers.echo import EchoProvider
from ai_gateway.infrastructure.providers.registry import DefaultProviderRegistry
from ai_gateway.infrastructure.rate_limiting.token_bucket import TokenBucketRateLimiter
from ai_gateway.infrastructure.resilience.circuit_breaker import InMemoryCircuitBreakerRegistry
from ai_gateway.infrastructure.tools.builtins import build_builtin_tools
from ai_gateway.infrastructure.tools.registry import InMemoryToolRegistry
from ai_gateway.observability.metrics import NullMetrics


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    InMemoryUnitOfWork.reset()
    get_settings.cache_clear()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant(name="acme", id=TenantId("11111111-1111-4111-8111-111111111111"))


@pytest.fixture
def principal(tenant: Tenant) -> Principal:
    return Principal(
        tenant_id=tenant.id,
        subject="user-1",
        roles=frozenset({Role.ADMIN}),
        auth_method="api_key",
    )


@pytest.fixture
def services() -> GatewayServices:
    clock = SystemClock()
    cache = InMemoryCache()
    metrics = NullMetrics()
    catalog = StaticModelCatalog()
    echo = EchoProvider(catalog=catalog)
    providers = DefaultProviderRegistry({echo.name: echo})
    breakers = InMemoryCircuitBreakerRegistry(failure_threshold=3, reset_timeout_seconds=1.0)
    tools = InMemoryToolRegistry(build_builtin_tools())
    cost = CostCalculator(catalog.price_book())
    return GatewayServices(
        uow_factory=InMemoryUnitOfWork,
        router=ModelRouter(catalog=catalog, providers=providers, breakers=breakers),
        executor=ProviderExecutor(
            providers=providers,
            breakers=breakers,
            clock=clock,
            metrics=metrics,
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.0, jitter=False),
        ),
        meter=UsageMeter(
            rate_limiter=TokenBucketRateLimiter(cache),
            cost_calculator=cost,
            clock=clock,
            metrics=metrics,
        ),
        guardrails=GuardrailService(),
        audit=AuditTrail(clock=clock),
        response_cache=ResponseCache(cache, metrics=metrics, ttl_seconds=60),
        embedding_cache=EmbeddingCache(cache, metrics=metrics, ttl_seconds=60),
        catalog=catalog,
        providers=providers,
        breakers=breakers,
        estimator=TokenEstimator(),
        clock=clock,
        metrics=metrics,
        tools=tools,
        default_timeout_seconds=5.0,
    )


@pytest.fixture
async def seeded_services(services: GatewayServices, tenant: Tenant) -> GatewayServices:
    async with services.uow_factory() as uow:
        await uow.tenants.upsert(tenant)
        await uow.commit()
    return services


@pytest.fixture
def request_context(principal: Principal, tenant: Tenant) -> RequestContext:
    return RequestContext(principal=principal, tenant=tenant)
