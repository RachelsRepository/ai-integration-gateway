"""Model routing service.

Bridges live infrastructure signals (catalogue, provider registry, circuit breakers) into
the pure :class:`~ai_gateway.domain.policies.routing.RoutingPolicy`.
"""

from __future__ import annotations

from ai_gateway.application.ports.model_catalog import ModelCatalog
from ai_gateway.application.ports.provider_registry import ProviderRegistry
from ai_gateway.application.ports.resilience import CircuitBreakerRegistry
from ai_gateway.domain.entities.tenant import RoutingPreferences
from ai_gateway.domain.policies.routing import (
    RoutingCandidate,
    RoutingDecision,
    RoutingPolicy,
    RoutingRequest,
    RoutingStrategy,
)
from ai_gateway.domain.value_objects.model import ModelCapability, ModelSpec
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus


class ModelRouter:
    """Chooses the model and failover chain that will serve a request."""

    def __init__(
        self,
        *,
        catalog: ModelCatalog,
        providers: ProviderRegistry,
        breakers: CircuitBreakerRegistry,
        policy: RoutingPolicy | None = None,
        region: str | None = None,
    ) -> None:
        """Initialise the router.

        Args:
            catalog: Source of model specifications.
            providers: Registry supplying configured adapters and health observations.
            breakers: Circuit breaker registry consulted before routing.
            policy: Routing policy; a default-weighted policy when omitted.
            region: Deployment region reported for data-residency matching.
        """
        self._catalog = catalog
        self._providers = providers
        self._breakers = breakers
        self._policy = policy or RoutingPolicy()
        self._region = region

    def route(
        self,
        *,
        preferences: RoutingPreferences,
        capabilities: frozenset[ModelCapability],
        strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        requested_model: str | None = None,
        estimated_prompt_tokens: int = 0,
        max_output_tokens: int = 512,
        excluded_models: frozenset[str] = frozenset(),
        max_candidates: int = 4,
    ) -> RoutingDecision:
        """Select a primary model and its fallback chain.

        Args:
            preferences: Tenant routing constraints.
            capabilities: Capabilities the model must advertise.
            strategy: Ranking objective.
            requested_model: Explicit ``provider/model`` pin.
            estimated_prompt_tokens: Prompt size used for context-window filtering.
            max_output_tokens: Requested completion budget.
            excluded_models: Models already attempted and failed.
            max_candidates: Maximum length of the returned chain.

        Returns:
            The routing decision.
        """
        request = RoutingRequest(
            required_capabilities=capabilities,
            strategy=strategy,
            requested_model=requested_model,
            estimated_prompt_tokens=estimated_prompt_tokens,
            max_output_tokens=max_output_tokens,
            preferences=preferences,
            excluded_models=excluded_models,
            max_candidates=max_candidates,
        )
        return self._policy.select(self.candidates(), request)

    def candidates(self) -> list[RoutingCandidate]:
        """Build routing candidates from the catalogue and live health signals.

        Returns:
            One candidate per configured, catalogued model.
        """
        candidates: list[RoutingCandidate] = []
        for index, spec in enumerate(self._catalog.all()):
            if not self._providers.has(spec.provider):
                continue
            candidates.append(self._build_candidate(spec, index))
        return candidates

    def _build_candidate(self, spec: ModelSpec, index: int) -> RoutingCandidate:
        provider = spec.provider
        breaker = self._breakers.get(provider.value)
        snapshot = breaker.snapshot()
        status = self._providers.status(provider)
        if snapshot.is_open:
            status = ProviderStatus.UNAVAILABLE
        return RoutingCandidate(
            spec=spec,
            status=status,
            circuit_open=snapshot.is_open,
            observed_latency_ms=self._providers.observed_latency_ms(provider),
            error_rate=max(self._providers.observed_error_rate(provider), snapshot.error_rate),
            region=spec.metadata.get("region", self._region),
            priority=int(spec.metadata.get("priority", str(index))),
        )

    def provider_status(self, provider: ProviderName) -> ProviderStatus:
        """Return the effective status of a provider, accounting for its breaker.

        Args:
            provider: Provider identifier.

        Returns:
            ``UNAVAILABLE`` when the breaker is open, otherwise the observed status.
        """
        if self._breakers.get(provider.value).snapshot().is_open:
            return ProviderStatus.UNAVAILABLE
        return self._providers.status(provider)


__all__ = ["ModelRouter"]
