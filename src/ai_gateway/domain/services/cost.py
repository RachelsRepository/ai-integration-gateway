"""Cost calculation domain service."""

from __future__ import annotations

from decimal import Decimal

from ai_gateway.domain.errors import NotFoundError
from ai_gateway.domain.value_objects.model import ModelRef, ModelSpec
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.tokens import TokenUsage


class CostCalculator:
    """Computes the monetary cost of model usage from a static price book.

    The calculator is deliberately provider-agnostic: providers report token counts, the
    catalogue supplies prices, and this service performs exact decimal arithmetic.
    """

    def __init__(
        self, price_book: dict[str, ModelSpec], *, markup_percent: Decimal | None = None
    ) -> None:
        """Initialise the calculator.

        Args:
            price_book: Model specifications keyed by qualified model reference.
            markup_percent: Optional platform markup applied on top of provider cost.
        """
        self._price_book = price_book
        self._markup = markup_percent or Decimal("0")

    def spec_for(self, model: ModelRef) -> ModelSpec:
        """Look up the specification for a model.

        Args:
            model: Qualified model reference.

        Returns:
            The model specification.

        Raises:
            NotFoundError: If the model is absent from the price book.
        """
        spec = self._price_book.get(model.qualified)
        if spec is None:
            raise NotFoundError(
                "Model is not present in the price book",
                details={"model": model.qualified},
            )
        return spec

    def calculate(self, model: ModelRef, usage: TokenUsage) -> Money:
        """Compute the billable cost of a single call.

        Args:
            model: Model that served the call.
            usage: Token counters reported by the provider.

        Returns:
            The billable cost including any configured markup.
        """
        base = self.spec_for(model).estimate_cost(usage)
        if self._markup == 0:
            return base
        multiplier = Decimal(1) + (self._markup / Decimal(100))
        return Money(base.amount * multiplier, base.currency)

    def estimate(self, model: ModelRef, *, prompt_tokens: int, max_output_tokens: int) -> Money:
        """Project the worst-case cost of a call before it is dispatched.

        Args:
            model: Candidate model.
            prompt_tokens: Estimated prompt size.
            max_output_tokens: Requested completion budget.

        Returns:
            The projected upper-bound cost.
        """
        usage = TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=max_output_tokens)
        return self.calculate(model, usage)

    def known_models(self) -> list[str]:
        """Return every qualified model reference present in the price book."""
        return sorted(self._price_book)


__all__ = ["CostCalculator"]
