"""Default provider registry with rolling health observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ai_gateway.application.ports.llm_provider import LLMProvider
from ai_gateway.domain.errors import NotFoundError
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus


class DefaultProviderRegistry:
    """Resolves provider identifiers to configured adapters."""

    def __init__(self, providers: Mapping[ProviderName, LLMProvider]) -> None:
        """Initialise the registry.

        Args:
            providers: Configured adapters keyed by provider name.
        """
        self._providers = dict(providers)
        self._status = dict.fromkeys(self._providers, ProviderStatus.HEALTHY)
        self._latency_ema: dict[ProviderName, float] = {}
        self._error_ema: dict[ProviderName, float] = {}
        self._alpha = 0.3

    def get(self, provider: ProviderName) -> LLMProvider:
        """Fetch a configured adapter.

        Args:
            provider: Provider identifier.

        Returns:
            The adapter.

        Raises:
            NotFoundError: If the provider is not configured.
        """
        adapter = self._providers.get(provider)
        if adapter is None:
            raise NotFoundError("Provider is not configured", details={"provider": provider.value})
        return adapter

    def has(self, provider: ProviderName) -> bool:
        """Report whether an adapter is configured."""
        return provider in self._providers

    def configured(self) -> Sequence[ProviderName]:
        """Return every configured provider identifier."""
        return tuple(sorted(self._providers, key=lambda p: p.value))

    def status(self, provider: ProviderName) -> ProviderStatus:
        """Return the last observed status of a provider."""
        return self._status.get(provider, ProviderStatus.UNAVAILABLE)

    def record_status(self, provider: ProviderName, status: ProviderStatus) -> None:
        """Update the cached status of a provider."""
        self._status[provider] = status

    def observed_latency_ms(self, provider: ProviderName) -> int | None:
        """Return the rolling latency observation for a provider."""
        value = self._latency_ema.get(provider)
        return int(value) if value is not None else None

    def observed_error_rate(self, provider: ProviderName) -> float:
        """Return the rolling error rate for a provider."""
        return self._error_ema.get(provider, 0.0)

    def record_outcome(self, provider: ProviderName, *, success: bool, latency_ms: int) -> None:
        """Fold a call outcome into the rolling health observations."""
        previous_latency = self._latency_ema.get(provider, float(latency_ms))
        self._latency_ema[provider] = (
            self._alpha * float(latency_ms) + (1 - self._alpha) * previous_latency
        )
        previous_error = self._error_ema.get(provider, 0.0)
        sample = 0.0 if success else 1.0
        self._error_ema[provider] = self._alpha * sample + (1 - self._alpha) * previous_error

    async def aclose(self) -> None:
        """Close every configured adapter."""
        for adapter in self._providers.values():
            await adapter.aclose()


__all__ = ["DefaultProviderRegistry"]
