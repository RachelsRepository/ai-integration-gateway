"""Static model catalogue and price book."""

from __future__ import annotations

from collections.abc import Sequence

from ai_gateway.domain.value_objects.model import (
    ModelCapability,
    ModelRef,
    ModelSpec,
    ModelTier,
)
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName


def _chat(
    provider: ProviderName,
    name: str,
    *,
    input_cost: str,
    output_cost: str,
    context: int,
    max_out: int,
    tier: ModelTier,
    latency: int,
    extra: frozenset[ModelCapability] = frozenset(),
    cached_input: str | None = None,
    priority: int = 100,
) -> ModelSpec:
    capabilities = frozenset(
        {
            ModelCapability.CHAT,
            ModelCapability.STREAMING,
            ModelCapability.TOOL_CALLING,
            ModelCapability.JSON_MODE,
            *extra,
        }
    )
    return ModelSpec(
        ref=ModelRef(provider, name),
        capabilities=capabilities,
        context_window=context,
        max_output_tokens=max_out,
        input_cost_per_1k=Money.of(input_cost),
        output_cost_per_1k=Money.of(output_cost),
        cached_input_cost_per_1k=Money.of(cached_input) if cached_input else None,
        expected_latency_ms=latency,
        tier=tier,
        metadata={"priority": str(priority)},
    )


def _embed(
    provider: ProviderName,
    name: str,
    *,
    input_cost: str,
    dimensions: int,
    context: int = 8192,
) -> ModelSpec:
    return ModelSpec(
        ref=ModelRef(provider, name),
        capabilities=frozenset({ModelCapability.EMBEDDINGS}),
        context_window=context,
        max_output_tokens=1,
        input_cost_per_1k=Money.of(input_cost),
        output_cost_per_1k=Money.zero(),
        expected_latency_ms=200,
        tier=ModelTier.ECONOMY,
        embedding_dimensions=dimensions,
    )


def default_catalog() -> tuple[ModelSpec, ...]:
    """Return the built-in model catalogue.

    Returns:
        Every model the gateway knows how to price and route.
    """
    return (
        _chat(
            ProviderName.ECHO,
            "echo-1",
            input_cost="0",
            output_cost="0",
            context=32_768,
            max_out=4_096,
            tier=ModelTier.ECONOMY,
            latency=5,
            priority=0,
        ),
        _embed(ProviderName.ECHO, "echo-embed", input_cost="0", dimensions=8),
        _chat(
            ProviderName.OPENAI,
            "gpt-4o-mini",
            input_cost="0.00015",
            output_cost="0.0006",
            context=128_000,
            max_out=16_384,
            tier=ModelTier.ECONOMY,
            latency=800,
            cached_input="0.000075",
            priority=10,
        ),
        _chat(
            ProviderName.OPENAI,
            "gpt-4o",
            input_cost="0.0025",
            output_cost="0.01",
            context=128_000,
            max_out=16_384,
            tier=ModelTier.STANDARD,
            latency=1200,
            extra=frozenset({ModelCapability.VISION}),
            priority=20,
        ),
        _embed(
            ProviderName.OPENAI, "text-embedding-3-small", input_cost="0.00002", dimensions=1536
        ),
        _chat(
            ProviderName.ANTHROPIC,
            "claude-3-5-haiku-latest",
            input_cost="0.0008",
            output_cost="0.004",
            context=200_000,
            max_out=8_192,
            tier=ModelTier.ECONOMY,
            latency=900,
            extra=frozenset({ModelCapability.LONG_CONTEXT}),
            priority=15,
        ),
        _chat(
            ProviderName.ANTHROPIC,
            "claude-3-5-sonnet-latest",
            input_cost="0.003",
            output_cost="0.015",
            context=200_000,
            max_out=8_192,
            tier=ModelTier.PREMIUM,
            latency=1500,
            extra=frozenset({ModelCapability.LONG_CONTEXT, ModelCapability.VISION}),
            priority=25,
        ),
        _chat(
            ProviderName.GOOGLE,
            "gemini-1.5-flash",
            input_cost="0.000075",
            output_cost="0.0003",
            context=1_000_000,
            max_out=8_192,
            tier=ModelTier.ECONOMY,
            latency=700,
            extra=frozenset({ModelCapability.LONG_CONTEXT, ModelCapability.VISION}),
            priority=12,
        ),
        _chat(
            ProviderName.GOOGLE,
            "gemini-1.5-pro",
            input_cost="0.00125",
            output_cost="0.005",
            context=2_000_000,
            max_out=8_192,
            tier=ModelTier.PREMIUM,
            latency=1600,
            extra=frozenset({ModelCapability.LONG_CONTEXT, ModelCapability.VISION}),
            priority=30,
        ),
        _embed(ProviderName.GOOGLE, "text-embedding-004", input_cost="0.00001", dimensions=768),
        _chat(
            ProviderName.AZURE_OPENAI,
            "gpt-4o-mini",
            input_cost="0.00015",
            output_cost="0.0006",
            context=128_000,
            max_out=16_384,
            tier=ModelTier.ECONOMY,
            latency=900,
            priority=11,
        ),
        _chat(
            ProviderName.BEDROCK,
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            input_cost="0.003",
            output_cost="0.015",
            context=200_000,
            max_out=8_192,
            tier=ModelTier.PREMIUM,
            latency=1800,
            extra=frozenset({ModelCapability.LONG_CONTEXT}),
            priority=35,
        ),
    )


class StaticModelCatalog:
    """In-process model catalogue backed by the built-in price book."""

    def __init__(self, specs: Sequence[ModelSpec] | None = None) -> None:
        """Initialise the catalogue.

        Args:
            specs: Model specifications; the built-in catalogue when omitted.
        """
        self._specs = tuple(specs or default_catalog())
        self._by_ref = {spec.ref.qualified: spec for spec in self._specs}

    def all(self) -> Sequence[ModelSpec]:
        """Return every registered model specification."""
        return self._specs

    def get(self, ref: ModelRef) -> ModelSpec | None:
        """Fetch a specification by reference.

        Args:
            ref: Qualified model reference.

        Returns:
            The specification, or ``None``.
        """
        return self._by_ref.get(ref.qualified)

    def for_provider(self, provider: ProviderName) -> Sequence[ModelSpec]:
        """Return models hosted by one provider.

        Args:
            provider: Provider identifier.

        Returns:
            The provider's models.
        """
        return tuple(spec for spec in self._specs if spec.provider is provider)

    def price_book(self) -> dict[str, ModelSpec]:
        """Return specifications keyed by qualified reference."""
        return dict(self._by_ref)


__all__ = ["StaticModelCatalog", "default_catalog"]
