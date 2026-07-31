"""Model routing policy.

Routing is a pure function of (request requirements, tenant policy, catalogue, observed
provider health). Keeping it free of I/O makes the most business-critical decision in the
platform exhaustively testable and trivially auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from ai_gateway.domain.entities.tenant import RoutingPreferences
from ai_gateway.domain.errors import NoProviderAvailableError
from ai_gateway.domain.value_objects.model import ModelCapability, ModelRef, ModelSpec, ModelTier
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus

_TIER_ORDER: dict[ModelTier, int] = {
    ModelTier.ECONOMY: 0,
    ModelTier.STANDARD: 1,
    ModelTier.PREMIUM: 2,
}
_TIER_QUALITY_SCORE: dict[ModelTier, float] = {
    ModelTier.ECONOMY: 0.4,
    ModelTier.STANDARD: 0.7,
    ModelTier.PREMIUM: 1.0,
}
_STATUS_PENALTY: dict[ProviderStatus, float] = {
    ProviderStatus.HEALTHY: 0.0,
    ProviderStatus.DEGRADED: 0.35,
    ProviderStatus.UNAVAILABLE: 1.0,
}
_EPSILON = 1e-9


class RoutingStrategy(StrEnum):
    """Objective used to rank routing candidates."""

    COST_OPTIMIZED = "cost_optimized"
    LATENCY_OPTIMIZED = "latency_optimized"
    QUALITY_OPTIMIZED = "quality_optimized"
    BALANCED = "balanced"
    FAILOVER = "failover"
    """Preserve the declared fallback order and only skip unhealthy candidates."""


@dataclass(frozen=True, slots=True)
class RoutingCandidate:
    """A model that could serve a request, together with live health signals.

    Attributes:
        spec: Static model metadata.
        status: Observed provider health.
        circuit_open: Whether the gateway circuit breaker is currently open.
        observed_latency_ms: Rolling p95 latency observed by the gateway.
        error_rate: Rolling error rate in the interval ``[0, 1]``.
        region: Deployment region, used for data-residency matching.
        priority: Declared fallback order; lower values are tried first.
    """

    spec: ModelSpec
    status: ProviderStatus = ProviderStatus.HEALTHY
    circuit_open: bool = False
    observed_latency_ms: int | None = None
    error_rate: float = 0.0
    region: str | None = None
    priority: int = 100

    @property
    def ref(self) -> ModelRef:
        """Return the candidate's qualified model reference."""
        return self.spec.ref

    @property
    def provider(self) -> ProviderName:
        """Return the candidate's provider."""
        return self.spec.provider

    @property
    def effective_latency_ms(self) -> int:
        """Return observed latency when available, otherwise the published expectation."""
        return self.observed_latency_ms or self.spec.expected_latency_ms

    @property
    def is_available(self) -> bool:
        """Return ``True`` when the candidate may currently receive traffic."""
        return self.status.is_routable and not self.circuit_open


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    """The requirements a routing decision must satisfy.

    Attributes:
        required_capabilities: Capabilities the model must advertise.
        strategy: Ranking objective.
        requested_model: Explicit ``provider/model`` pin requested by the caller.
        estimated_prompt_tokens: Prompt size used for context-window filtering.
        max_output_tokens: Requested completion budget.
        preferences: Tenant routing constraints.
        excluded_models: Qualified references already attempted and failed.
        max_candidates: Maximum length of the returned fallback chain.
    """

    required_capabilities: frozenset[ModelCapability] = frozenset({ModelCapability.CHAT})
    strategy: RoutingStrategy = RoutingStrategy.BALANCED
    requested_model: str | None = None
    estimated_prompt_tokens: int = 0
    max_output_tokens: int = 512
    preferences: RoutingPreferences = field(default_factory=RoutingPreferences)
    excluded_models: frozenset[str] = frozenset()
    max_candidates: int = 4


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The outcome of evaluating the routing policy.

    Attributes:
        selected: The primary candidate.
        fallbacks: Ordered alternatives to try if the primary fails.
        strategy: Strategy that produced the ranking.
        reason: Short, human readable justification recorded in traces and audits.
        rejected: Mapping of qualified model reference to rejection reason.
        estimated_cost: Projected cost of the selected candidate.
    """

    selected: RoutingCandidate
    fallbacks: tuple[RoutingCandidate, ...]
    strategy: RoutingStrategy
    reason: str
    rejected: dict[str, str] = field(default_factory=dict)
    estimated_cost: Money = field(default_factory=Money.zero)

    @property
    def chain(self) -> tuple[RoutingCandidate, ...]:
        """Return the primary candidate followed by its fallbacks."""
        return (self.selected, *self.fallbacks)


class RoutingPolicy:
    """Selects the model that should serve a request.

    The policy filters candidates against hard constraints, ranks the survivors using the
    requested strategy and returns an ordered failover chain.

    Attributes:
        cost_weight: Relative importance of projected cost in ``BALANCED`` mode.
        latency_weight: Relative importance of latency in ``BALANCED`` mode.
        quality_weight: Relative importance of model tier in ``BALANCED`` mode.
        reliability_weight: Relative importance of observed reliability.
    """

    def __init__(
        self,
        *,
        cost_weight: float = 0.35,
        latency_weight: float = 0.25,
        quality_weight: float = 0.25,
        reliability_weight: float = 0.15,
    ) -> None:
        """Initialise the policy with scoring weights.

        Args:
            cost_weight: Relative importance of projected cost.
            latency_weight: Relative importance of latency.
            quality_weight: Relative importance of model tier.
            reliability_weight: Relative importance of observed reliability.
        """
        self.cost_weight = cost_weight
        self.latency_weight = latency_weight
        self.quality_weight = quality_weight
        self.reliability_weight = reliability_weight

    def select(
        self, candidates: list[RoutingCandidate], request: RoutingRequest
    ) -> RoutingDecision:
        """Choose a primary model and an ordered fallback chain.

        Args:
            candidates: Every model the gateway knows about.
            request: Routing requirements.

        Returns:
            The routing decision.

        Raises:
            NoProviderAvailableError: If no candidate satisfies the constraints.
        """
        rejected: dict[str, str] = {}
        eligible = [c for c in candidates if self._accept(c, request, rejected)]
        if not eligible:
            raise NoProviderAvailableError(
                "No provider satisfies the routing constraints",
                details={
                    "rejected": rejected,
                    "strategy": request.strategy.value,
                    "requested_model": request.requested_model,
                },
            )

        ranked = self._rank(eligible, request)
        selected, *rest = ranked
        fallbacks = tuple(rest[: max(request.max_candidates - 1, 0)])
        return RoutingDecision(
            selected=selected,
            fallbacks=fallbacks,
            strategy=request.strategy,
            reason=self._explain(selected, request),
            rejected=rejected,
            estimated_cost=self._projected_cost(selected, request),
        )

    # ---------------------------------------------------------------- filtering
    def _accept(
        self,
        candidate: RoutingCandidate,
        request: RoutingRequest,
        rejected: dict[str, str],
    ) -> bool:
        key = candidate.ref.qualified
        reason = self._rejection_reason(candidate, request)
        if reason is None:
            return True
        rejected[key] = reason
        return False

    def _rejection_reason(  # noqa: PLR0911, PLR0912 - a flat filter chain beats nesting
        self, candidate: RoutingCandidate, request: RoutingRequest
    ) -> str | None:
        key = candidate.ref.qualified
        prefs = request.preferences

        if key in request.excluded_models:
            return "already_attempted"
        if not candidate.is_available:
            return "circuit_open" if candidate.circuit_open else "provider_unavailable"
        if candidate.spec.deprecated:
            return "model_deprecated"
        if request.requested_model and key != request.requested_model:
            return "model_not_requested"
        if not candidate.spec.supports(*request.required_capabilities):
            return "missing_capability"
        if not prefs.permits_provider(candidate.provider):
            return "provider_not_permitted_for_tenant"
        if not prefs.permits_model(key):
            return "model_not_permitted_for_tenant"
        if _TIER_ORDER[candidate.spec.tier] > _TIER_ORDER[prefs.max_tier]:
            return "tier_above_tenant_limit"
        if prefs.require_streaming_support and not candidate.spec.supports(
            ModelCapability.STREAMING
        ):
            return "streaming_not_supported"
        if prefs.data_residency and candidate.region != prefs.data_residency:
            return "data_residency_mismatch"
        if not candidate.spec.fits_context(
            request.estimated_prompt_tokens, request.max_output_tokens
        ):
            return "context_window_too_small"
        if prefs.max_cost_per_request is not None:
            projected = self._projected_cost(candidate, request)
            if projected.amount > prefs.max_cost_per_request.amount:
                return "projected_cost_above_tenant_limit"
        return None

    # ---------------------------------------------------------------- ranking
    def _rank(
        self, candidates: list[RoutingCandidate], request: RoutingRequest
    ) -> list[RoutingCandidate]:
        if request.strategy is RoutingStrategy.FAILOVER:
            return sorted(candidates, key=lambda c: (c.priority, c.ref.qualified))

        scores = {c.ref.qualified: self._score(c, candidates, request) for c in candidates}
        preferred = request.preferences.preferred_provider

        def sort_key(candidate: RoutingCandidate) -> tuple[int, float, str]:
            preference_rank = 0 if preferred and candidate.provider is preferred else 1
            return (preference_rank, scores[candidate.ref.qualified], candidate.ref.qualified)

        return sorted(candidates, key=sort_key)

    def _score(
        self,
        candidate: RoutingCandidate,
        population: list[RoutingCandidate],
        request: RoutingRequest,
    ) -> float:
        """Return a normalised penalty score where lower is better."""
        cost_penalty = self._normalised_cost(candidate, population, request)
        latency_penalty = self._normalised_latency(candidate, population)
        quality_penalty = 1.0 - _TIER_QUALITY_SCORE[candidate.spec.tier]
        reliability_penalty = min(max(candidate.error_rate, 0.0), 1.0)
        health_penalty = _STATUS_PENALTY[candidate.status]

        if request.strategy is RoutingStrategy.COST_OPTIMIZED:
            weights = (0.75, 0.05, 0.05, 0.15)
        elif request.strategy is RoutingStrategy.LATENCY_OPTIMIZED:
            weights = (0.05, 0.75, 0.05, 0.15)
        elif request.strategy is RoutingStrategy.QUALITY_OPTIMIZED:
            weights = (0.05, 0.05, 0.75, 0.15)
        else:
            weights = (
                self.cost_weight,
                self.latency_weight,
                self.quality_weight,
                self.reliability_weight,
            )

        return (
            weights[0] * cost_penalty
            + weights[1] * latency_penalty
            + weights[2] * quality_penalty
            + weights[3] * reliability_penalty
            + health_penalty
        )

    def _normalised_cost(
        self,
        candidate: RoutingCandidate,
        population: list[RoutingCandidate],
        request: RoutingRequest,
    ) -> float:
        costs = [float(self._projected_cost(c, request).amount) for c in population]
        value = float(self._projected_cost(candidate, request).amount)
        return _normalise(value, costs)

    @staticmethod
    def _normalised_latency(
        candidate: RoutingCandidate, population: list[RoutingCandidate]
    ) -> float:
        latencies = [float(c.effective_latency_ms) for c in population]
        return _normalise(float(candidate.effective_latency_ms), latencies)

    @staticmethod
    def _projected_cost(candidate: RoutingCandidate, request: RoutingRequest) -> Money:
        thousand = Decimal(1000)
        prompt = Decimal(request.estimated_prompt_tokens) / thousand
        completion = Decimal(request.max_output_tokens) / thousand
        spec = candidate.spec
        amount = (
            spec.input_cost_per_1k.amount * prompt + spec.output_cost_per_1k.amount * completion
        )
        return Money(amount, spec.input_cost_per_1k.currency)

    @staticmethod
    def _explain(candidate: RoutingCandidate, request: RoutingRequest) -> str:
        if request.requested_model:
            return f"explicit model pin {candidate.ref.qualified}"
        return (
            f"{request.strategy.value} selection: {candidate.ref.qualified} "
            f"(tier={candidate.spec.tier.value}, latency={candidate.effective_latency_ms}ms, "
            f"status={candidate.status.value})"
        )


def _normalise(value: float, population: list[float]) -> float:
    """Scale a value into ``[0, 1]`` against the population's min and max.

    Args:
        value: The value to normalise.
        population: All observed values, including ``value``.

    Returns:
        Zero when ``value`` is the minimum, one when it is the maximum.
    """
    if not population:
        return 0.0
    low, high = min(population), max(population)
    if high - low < _EPSILON:
        return 0.0
    return (value - low) / (high - low)


__all__ = [
    "RoutingCandidate",
    "RoutingDecision",
    "RoutingPolicy",
    "RoutingRequest",
    "RoutingStrategy",
]
