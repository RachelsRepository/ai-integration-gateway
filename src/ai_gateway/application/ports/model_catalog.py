"""Model catalogue port."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ai_gateway.domain.value_objects.model import ModelRef, ModelSpec
from ai_gateway.domain.value_objects.provider import ProviderName


@runtime_checkable
class ModelCatalog(Protocol):
    """Supplies the set of models the gateway can route to."""

    def all(self) -> Sequence[ModelSpec]:
        """Return every registered model specification."""
        ...

    def get(self, ref: ModelRef) -> ModelSpec | None:
        """Fetch a specification by reference.

        Args:
            ref: Qualified model reference.

        Returns:
            The specification, or ``None`` when unknown.
        """
        ...

    def for_provider(self, provider: ProviderName) -> Sequence[ModelSpec]:
        """Return the models hosted by one provider.

        Args:
            provider: Provider identifier.

        Returns:
            The provider's model specifications.
        """
        ...

    def price_book(self) -> dict[str, ModelSpec]:
        """Return specifications keyed by qualified reference, for cost calculation."""
        ...


__all__ = ["ModelCatalog"]
