"""Model identity, capability and specification value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from ai_gateway.domain.errors import ValidationError
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.domain.value_objects.tokens import TokenUsage


class ModelCapability(StrEnum):
    """Discrete capabilities a model may advertise."""

    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    STREAMING = "streaming"
    TOOL_CALLING = "tool_calling"
    JSON_MODE = "json_mode"
    VISION = "vision"
    LONG_CONTEXT = "long_context"
    REASONING = "reasoning"


class ModelTier(StrEnum):
    """Coarse cost/quality tier used by tenant routing policies."""

    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A fully qualified reference to a model hosted by a provider.

    Attributes:
        provider: The provider hosting the model.
        name: The provider-native model identifier.
    """

    provider: ProviderName
    name: str

    def __post_init__(self) -> None:
        """Validate the model name.

        Raises:
            ValidationError: If the model name is empty.
        """
        if not self.name or not self.name.strip():
            raise ValidationError("Model name must not be empty")

    @classmethod
    def parse(cls, qualified: str) -> ModelRef:
        """Parse a ``provider/model`` string.

        Args:
            qualified: A qualified model reference such as ``openai/gpt-4o-mini``.

        Returns:
            The parsed :class:`ModelRef`.

        Raises:
            ValidationError: If the string is not a qualified reference.
        """
        if "/" not in qualified:
            raise ValidationError(
                "Model reference must be in 'provider/model' form",
                details={"value": qualified},
            )
        provider_part, _, model_part = qualified.partition("/")
        try:
            provider = ProviderName.parse(provider_part)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValidationError(f"Unknown provider {provider_part!r}") from exc
        return cls(provider=provider, name=model_part)

    @property
    def qualified(self) -> str:
        """Return the canonical ``provider/model`` representation."""
        return f"{self.provider.value}/{self.name}"

    def __str__(self) -> str:
        """Return the canonical qualified name."""
        return self.qualified


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Static metadata describing a routable model.

    Attributes:
        ref: The qualified model reference.
        capabilities: Capabilities advertised by the model.
        context_window: Maximum combined prompt and completion tokens.
        max_output_tokens: Maximum tokens the model may emit in one response.
        input_cost_per_1k: Price per one thousand prompt tokens.
        output_cost_per_1k: Price per one thousand completion tokens.
        cached_input_cost_per_1k: Price per one thousand cached prompt tokens.
        expected_latency_ms: Rolling expectation used by latency-aware routing.
        tier: Coarse cost/quality tier.
        embedding_dimensions: Vector width when the model produces embeddings.
        deprecated: Whether the model should be avoided for new traffic.
    """

    ref: ModelRef
    capabilities: frozenset[ModelCapability]
    context_window: int
    max_output_tokens: int
    input_cost_per_1k: Money
    output_cost_per_1k: Money
    cached_input_cost_per_1k: Money | None = None
    expected_latency_ms: int = 1_000
    tier: ModelTier = ModelTier.STANDARD
    embedding_dimensions: int | None = None
    deprecated: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate structural invariants.

        Raises:
            ValidationError: If the context window or output budget is not positive.
        """
        if self.context_window <= 0:
            raise ValidationError("context_window must be positive")
        if self.max_output_tokens <= 0:
            raise ValidationError("max_output_tokens must be positive")

    @property
    def provider(self) -> ProviderName:
        """Return the provider hosting this model."""
        return self.ref.provider

    def supports(self, *capabilities: ModelCapability) -> bool:
        """Report whether the model advertises every requested capability.

        Args:
            *capabilities: Capabilities the caller requires.

        Returns:
            ``True`` when all capabilities are supported.
        """
        return set(capabilities).issubset(self.capabilities)

    def estimate_cost(self, usage: TokenUsage) -> Money:
        """Compute the cost of a call from its token usage.

        Cached prompt tokens are billed at the discounted cached rate when the provider
        publishes one, otherwise at the standard input rate.

        Args:
            usage: Token counters reported by the provider.

        Returns:
            The total cost of the call.
        """
        thousand = Decimal(1000)
        billable_input = Decimal(usage.billable_prompt_tokens) / thousand
        cached_input = Decimal(usage.cached_prompt_tokens) / thousand
        output = Decimal(usage.completion_tokens + usage.reasoning_tokens) / thousand

        cached_rate = self.cached_input_cost_per_1k or self.input_cost_per_1k
        total = (
            self.input_cost_per_1k.amount * billable_input
            + cached_rate.amount * cached_input
            + self.output_cost_per_1k.amount * output
        )
        return Money(total, self.input_cost_per_1k.currency)

    def fits_context(self, prompt_tokens: int, requested_output_tokens: int) -> bool:
        """Report whether a request fits inside the model's context window.

        Args:
            prompt_tokens: Estimated prompt token count.
            requested_output_tokens: Requested completion budget.

        Returns:
            ``True`` when the request fits.
        """
        return (
            prompt_tokens + requested_output_tokens <= self.context_window
            and requested_output_tokens <= self.max_output_tokens
        )


__all__ = ["ModelCapability", "ModelRef", "ModelSpec", "ModelTier"]
