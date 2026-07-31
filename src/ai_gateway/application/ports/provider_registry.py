"""Provider registry port."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ai_gateway.application.ports.llm_provider import LLMProvider
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus


@runtime_checkable
class ProviderRegistry(Protocol):
    """Resolves provider identifiers to configured adapters."""

    def get(self, provider: ProviderName) -> LLMProvider:
        """Fetch a configured adapter.

        Args:
            provider: Provider identifier.

        Returns:
            The adapter.
        """
        ...

    def has(self, provider: ProviderName) -> bool:
        """Report whether an adapter is configured.

        Args:
            provider: Provider identifier.

        Returns:
            ``True`` when the provider is available for routing.
        """
        ...

    def configured(self) -> Sequence[ProviderName]:
        """Return every configured provider identifier."""
        ...

    def status(self, provider: ProviderName) -> ProviderStatus:
        """Return the last observed status of a provider.

        Args:
            provider: Provider identifier.

        Returns:
            The cached health status.
        """
        ...

    def record_status(self, provider: ProviderName, status: ProviderStatus) -> None:
        """Update the cached status of a provider.

        Args:
            provider: Provider identifier.
            status: Newly observed status.
        """
        ...

    def observed_latency_ms(self, provider: ProviderName) -> int | None:
        """Return the rolling latency observation for a provider.

        Args:
            provider: Provider identifier.

        Returns:
            The observed latency, or ``None`` when no samples exist.
        """
        ...

    def observed_error_rate(self, provider: ProviderName) -> float:
        """Return the rolling error rate for a provider.

        Args:
            provider: Provider identifier.

        Returns:
            The error rate in ``[0, 1]``.
        """
        ...

    def record_outcome(self, provider: ProviderName, *, success: bool, latency_ms: int) -> None:
        """Fold a call outcome into the rolling health observations.

        Args:
            provider: Provider identifier.
            success: Whether the call succeeded.
            latency_ms: Observed latency.
        """
        ...

    async def aclose(self) -> None:
        """Close every configured adapter."""
        ...


__all__ = ["ProviderRegistry"]
