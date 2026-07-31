"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_gateway import __version__
from ai_gateway.api.errors import install_exception_handlers
from ai_gateway.api.middleware.correlation import CorrelationMiddleware
from ai_gateway.api.routes import agents, catalog, chat, embeddings, health, prompts
from ai_gateway.config.settings import Settings, get_settings
from ai_gateway.container import build_container
from ai_gateway.domain.entities.tenant import Role, Tenant
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.observability.logging import configure_logging, get_logger
from ai_gateway.observability.tracing import configure_tracing, shutdown_tracing

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: wire dependencies and seed local data.

    Args:
        app: FastAPI application.

    Yields:
        Control while the application is serving.
    """
    settings: Settings = app.state.settings
    container = await build_container(settings)
    app.state.container = container

    if settings.is_local:
        hasher = container.authenticator._hasher
        if hasher is not None:
            tenant = Tenant(name="demo", id=TenantId("00000000-0000-4000-8000-000000000001"))
            plaintext, api_key = hasher.mint(
                tenant_id=tenant.id, name="demo", roles=frozenset({Role.ADMIN})
            )
            async with container.services.uow_factory() as uow:
                existing = await uow.tenants.get(tenant.id)
                if existing is None:
                    await uow.tenants.upsert(tenant)
                    await uow.api_keys.add(api_key)
                    await uow.commit()
                    logger.info("demo_tenant_seeded", api_key_prefix=api_key.prefix)
                    app.state.demo_api_key = plaintext
                else:
                    await uow.rollback()

    logger.info(
        "gateway_started",
        environment=settings.environment.value,
        providers=[p.value for p in container.services.providers.configured()],
    )
    try:
        yield
    finally:
        await container.services.providers.aclose()
        shutdown_tracing()
        logger.info("gateway_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Optional settings override.

    Returns:
        The configured application.
    """
    settings = settings or get_settings()
    configure_logging(
        level=settings.observability.log_level,
        json_output=settings.observability.log_format == "json",
        service_name=settings.service_name,
        version=settings.version,
        environment=settings.environment.value,
    )
    configure_tracing(
        service_name=settings.service_name,
        environment=settings.environment.value,
        version=settings.version,
        enabled=settings.observability.tracing_enabled,
        otlp_endpoint=settings.observability.otlp_endpoint,
        sample_ratio=settings.observability.trace_sample_ratio,
        headers=settings.observability.otlp_headers,
    )

    app = FastAPI(
        title="AI Integration Gateway",
        description=(
            "Secure, multi-provider AI integration gateway providing a unified API for "
            "chat completions, embeddings, prompt management and agent execution."
        ),
        version=__version__,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
        root_path=settings.root_path,
    )
    app.state.settings = settings
    app.state.container = None  # type: ignore[assignment]

    if settings.security.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.security.cors_allowed_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(CorrelationMiddleware, header_name=settings.observability.correlation_header)
    install_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(embeddings.router)
    app.include_router(agents.router)
    app.include_router(prompts.router)
    app.include_router(catalog.router)
    return app


__all__ = ["create_app"]
