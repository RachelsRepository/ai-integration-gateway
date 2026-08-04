"""Composition root: wires adapters into the application layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_gateway.application.ports.cache import Cache
from ai_gateway.application.ports.dlq import DeadLetterQueue
from ai_gateway.application.ports.events import EventPublisher
from ai_gateway.application.ports.repositories import UnitOfWork
from ai_gateway.application.ports.resilience import CircuitBreakerRegistry
from ai_gateway.application.services.audit import AuditTrail
from ai_gateway.application.services.caching import EmbeddingCache, ResponseCache
from ai_gateway.application.services.execution import ProviderExecutor
from ai_gateway.application.services.guardrails import GuardrailService
from ai_gateway.application.services.metering import UsageMeter
from ai_gateway.application.services.quota_ledger import QuotaReservationLedger
from ai_gateway.application.services.router import ModelRouter
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.config.settings import PersistenceBackend, Settings
from ai_gateway.domain.policies.retry import RetryPolicy
from ai_gateway.domain.services.content_safety import (
    OutputFilter,
    PromptInjectionDetector,
    RiskLevel,
)
from ai_gateway.domain.services.cost import CostCalculator
from ai_gateway.domain.services.redaction import PiiRedactor
from ai_gateway.domain.services.token_estimation import TokenEstimator
from ai_gateway.infrastructure.cache.memory import InMemoryCache
from ai_gateway.infrastructure.cache.redis_cache import RedisCache, create_redis_client
from ai_gateway.infrastructure.clock import SystemClock
from ai_gateway.infrastructure.dlq.memory import InMemoryDeadLetterQueue
from ai_gateway.infrastructure.dlq.sqlalchemy import SqlDeadLetterQueue
from ai_gateway.infrastructure.events.kafka_publisher import KafkaEventPublisher
from ai_gateway.infrastructure.events.memory import InMemoryEventPublisher
from ai_gateway.infrastructure.persistence.database import create_engine, create_session_factory
from ai_gateway.infrastructure.persistence.memory import InMemoryUnitOfWork
from ai_gateway.infrastructure.persistence.sqlalchemy import SqlAlchemyUnitOfWork
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog
from ai_gateway.infrastructure.providers.factory import build_providers
from ai_gateway.infrastructure.providers.registry import DefaultProviderRegistry
from ai_gateway.infrastructure.rate_limiting.token_bucket import TokenBucketRateLimiter
from ai_gateway.infrastructure.resilience.circuit_breaker import InMemoryCircuitBreakerRegistry
from ai_gateway.infrastructure.resilience.redis_circuit_breaker import RedisCircuitBreakerRegistry
from ai_gateway.infrastructure.secrets.resolver import CompositeSecretResolver
from ai_gateway.infrastructure.security.api_keys import ApiKeyHasher
from ai_gateway.infrastructure.security.authenticator import Authenticator
from ai_gateway.infrastructure.security.jwt_validator import JwtValidator
from ai_gateway.infrastructure.tools.builtins import build_builtin_tools
from ai_gateway.infrastructure.tools.registry import InMemoryToolRegistry
from ai_gateway.observability.logging import get_logger
from ai_gateway.observability.metrics import NullMetrics, PrometheusMetrics

logger = get_logger(__name__)


@dataclass(slots=True)
class AppContainer:
    """Process-wide dependency container shared by the API and workers."""

    services: GatewayServices
    authenticator: Authenticator
    settings: Any
    engine: AsyncEngine | None = None
    event_publisher: EventPublisher | None = None
    dlq: DeadLetterQueue | None = None
    _redis_client: Any = field(default=None, repr=False)

    async def aclose(self) -> None:
        """Release process-wide adapters."""
        await self.services.providers.aclose()
        if self.event_publisher is not None:
            await self.event_publisher.stop()
        if self._redis_client is not None:
            await self._redis_client.aclose()
        if self.engine is not None:
            await self.engine.dispose()


async def build_container(settings: Settings) -> AppContainer:  # noqa: PLR0915
    """Build the fully wired application container.

    Args:
        settings: Application settings.

    Returns:
        The process-wide container.
    """
    clock = SystemClock()
    secrets = CompositeSecretResolver(allow_literals=settings.is_local)
    catalog = StaticModelCatalog()
    metrics: PrometheusMetrics | NullMetrics = (
        PrometheusMetrics() if settings.observability.metrics_enabled else NullMetrics()
    )
    providers = await build_providers(settings.providers, secrets, catalog=catalog)
    registry = DefaultProviderRegistry(providers)
    tools = InMemoryToolRegistry(build_builtin_tools())
    cost_calculator = CostCalculator(catalog.price_book())
    estimator = TokenEstimator()

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    redis_client: Any = None
    cache: Cache
    breakers: CircuitBreakerRegistry
    dlq: DeadLetterQueue
    if settings.persistence_backend is PersistenceBackend.POSTGRES:
        engine = create_engine(settings.database)
        session_factory = create_session_factory(engine)

        factory = session_factory

        def uow_factory() -> UnitOfWork:
            return SqlAlchemyUnitOfWork(factory)

        redis_client = create_redis_client(settings.redis.url)
        cache = RedisCache(redis_client)
        breakers = RedisCircuitBreakerRegistry(
            redis_client,
            failure_threshold=settings.resilience.circuit_failure_threshold,
            success_threshold=settings.resilience.circuit_success_threshold,
            reset_timeout_seconds=settings.resilience.circuit_reset_timeout_seconds,
            window_size=settings.resilience.circuit_window_size,
        )
        dlq = SqlDeadLetterQueue(factory)
        logger.info("persistence_backend", backend="postgres", cache="redis", dlq="postgres")
    else:

        def uow_factory() -> UnitOfWork:
            return InMemoryUnitOfWork()

        cache = InMemoryCache()
        breakers = InMemoryCircuitBreakerRegistry(
            failure_threshold=settings.resilience.circuit_failure_threshold,
            success_threshold=settings.resilience.circuit_success_threshold,
            reset_timeout_seconds=settings.resilience.circuit_reset_timeout_seconds,
            window_size=settings.resilience.circuit_window_size,
        )
        dlq = InMemoryDeadLetterQueue()
        logger.info("persistence_backend", backend="memory", cache="memory", dlq="memory")

    rate_limiter = TokenBucketRateLimiter(cache)
    reservation_ledger = QuotaReservationLedger(
        cache, fail_closed=settings.persistence_backend is PersistenceBackend.POSTGRES
    )
    router = ModelRouter(
        catalog=catalog,
        providers=registry,
        breakers=breakers,
        region=settings.region,
    )
    executor = ProviderExecutor(
        providers=registry,
        breakers=breakers,
        clock=clock,
        metrics=metrics,
        retry_policy=RetryPolicy(
            max_attempts=settings.resilience.retry_max_attempts,
            base_delay_seconds=settings.resilience.retry_base_delay_seconds,
            max_delay_seconds=settings.resilience.retry_max_delay_seconds,
            multiplier=settings.resilience.retry_multiplier,
        ),
    )
    guardrails = GuardrailService(
        detector=PromptInjectionDetector(
            block_threshold=RiskLevel(settings.security.injection_block_threshold)
        ),
        redactor=PiiRedactor(),
        output_filter=OutputFilter(block_on_secret=settings.security.output_filter_block_on_secret),
    )
    meter = UsageMeter(
        rate_limiter=rate_limiter,
        cost_calculator=cost_calculator,
        clock=clock,
        metrics=metrics,
        reservation_ledger=reservation_ledger,
    )
    response_cache = ResponseCache(
        cache,
        metrics=metrics,
        ttl_seconds=settings.redis.response_cache_ttl_seconds,
    )
    embedding_cache = EmbeddingCache(
        cache,
        metrics=metrics,
        ttl_seconds=settings.redis.embedding_cache_ttl_seconds,
    )

    services = GatewayServices(
        uow_factory=uow_factory,
        router=router,
        executor=executor,
        meter=meter,
        guardrails=guardrails,
        audit=AuditTrail(clock=clock),
        response_cache=response_cache,
        embedding_cache=embedding_cache,
        catalog=catalog,
        providers=registry,
        breakers=breakers,
        estimator=estimator,
        clock=clock,
        metrics=metrics,
        tools=tools,
        default_timeout_seconds=settings.resilience.provider_timeout_seconds,
    )

    pepper = await secrets.resolve(settings.auth.api_key_pepper_ref)
    hasher = ApiKeyHasher(pepper, prefix_length=settings.auth.api_key_prefix_length)
    jwt_validator = None
    if settings.auth.jwt_enabled:
        shared = await secrets.resolve_optional(settings.auth.jwt_shared_secret_ref)
        jwt_validator = JwtValidator(
            issuer=settings.auth.jwt_issuer,
            audience=settings.auth.jwt_audience,
            algorithms=settings.auth.jwt_algorithms,
            jwks_url=settings.auth.jwks_url,
            shared_secret=shared,
            tenant_claim=settings.auth.tenant_claim,
            roles_claim=settings.auth.roles_claim,
            scope_claim=settings.auth.scope_claim,
            leeway_seconds=settings.auth.jwt_leeway_seconds,
        )
    authenticator = Authenticator(
        api_key_hasher=hasher,
        jwt_validator=jwt_validator,
        clock=clock,
        api_keys_enabled=settings.auth.api_keys_enabled,
        jwt_enabled=settings.auth.jwt_enabled,
    )

    if settings.kafka.enabled:
        publisher: EventPublisher = KafkaEventPublisher(
            bootstrap_servers=settings.kafka.bootstrap_servers,
            client_id=settings.kafka.client_id,
            topic_prefix=settings.kafka.topic_prefix,
            enabled=True,
            linger_ms=settings.kafka.linger_ms,
            request_timeout_ms=settings.kafka.request_timeout_ms,
        )
    else:
        publisher = InMemoryEventPublisher()
    await publisher.start()

    return AppContainer(
        services=services,
        authenticator=authenticator,
        settings=settings,
        engine=engine,
        event_publisher=publisher,
        dlq=dlq,
        _redis_client=redis_client,
    )


async def bootstrap_demo_tenant(uow_factory: Callable[[], UnitOfWork], hasher: ApiKeyHasher) -> str:
    """Create a demo tenant and API key for local development.

    Args:
        uow_factory: Unit of work factory.
        hasher: API key hasher.

    Returns:
        The plaintext API key.
    """
    from ai_gateway.domain.entities.tenant import Role, Tenant
    from ai_gateway.domain.value_objects.identifiers import TenantId

    tenant = Tenant(name="demo", id=TenantId("00000000-0000-4000-8000-000000000001"))
    plaintext, api_key = hasher.mint(
        tenant_id=tenant.id, name="demo", roles=frozenset({Role.ADMIN})
    )
    async with uow_factory() as uow:
        await uow.tenants.upsert(tenant)
        await uow.api_keys.add(api_key)
        await uow.commit()
    return plaintext


__all__ = ["AppContainer", "bootstrap_demo_tenant", "build_container"]
