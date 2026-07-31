"""Model and provider discovery use cases."""

from __future__ import annotations

from ai_gateway.application.dto import ModelView, ProviderView, RequestContext
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.domain.entities.tenant import Permission
from ai_gateway.domain.value_objects.model import ModelCapability


class ListModelsUseCase:
    """Serves ``GET /v1/models``.

    The catalogue is filtered by tenant policy so that a caller only discovers models it
    is actually entitled to use.
    """

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators.
        """
        self._s = services

    def execute(
        self, context: RequestContext, *, capability: ModelCapability | None = None
    ) -> tuple[ModelView, ...]:
        """List routable models.

        Args:
            context: Request context.
            capability: Restrict the result to models advertising this capability.

        Returns:
            The model read models.
        """
        context.principal.require(Permission.MODELS_READ)
        preferences = context.tenant.routing
        views: list[ModelView] = []
        for spec in self._s.catalog.all():
            qualified = spec.ref.qualified
            if not preferences.permits_provider(spec.provider) or not preferences.permits_model(
                qualified
            ):
                continue
            if capability is not None and not spec.supports(capability):
                continue
            available = (
                self._s.providers.has(spec.provider)
                and self._s.router.provider_status(spec.provider).is_routable
            )
            views.append(
                ModelView(
                    id=qualified,
                    provider=spec.provider,
                    capabilities=tuple(sorted(spec.capabilities)),
                    context_window=spec.context_window,
                    max_output_tokens=spec.max_output_tokens,
                    input_cost_per_1k=spec.input_cost_per_1k,
                    output_cost_per_1k=spec.output_cost_per_1k,
                    tier=spec.tier.value,
                    expected_latency_ms=spec.expected_latency_ms,
                    available=available,
                    deprecated=spec.deprecated,
                )
            )
        return tuple(sorted(views, key=lambda v: v.id))


class ListProvidersUseCase:
    """Serves ``GET /v1/providers``."""

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators.
        """
        self._s = services

    def execute(self, context: RequestContext) -> tuple[ProviderView, ...]:
        """List configured providers and their live health.

        Args:
            context: Request context.

        Returns:
            The provider read models.
        """
        context.principal.require(Permission.PROVIDERS_READ)
        preferences = context.tenant.routing
        views: list[ProviderView] = []
        for provider in self._s.providers.configured():
            breaker = self._s.breakers.get(provider.value)
            views.append(
                ProviderView(
                    name=provider,
                    status=self._s.router.provider_status(provider),
                    circuit_state=breaker.snapshot().state.value,
                    models=tuple(
                        spec.ref.qualified for spec in self._s.catalog.for_provider(provider)
                    ),
                    observed_latency_ms=self._s.providers.observed_latency_ms(provider),
                    error_rate=self._s.providers.observed_error_rate(provider),
                    enabled_for_tenant=preferences.permits_provider(provider),
                )
            )
        return tuple(sorted(views, key=lambda v: v.name.value))


__all__ = ["ListModelsUseCase", "ListProvidersUseCase"]
