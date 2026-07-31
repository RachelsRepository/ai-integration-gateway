"""Monetary value object.

Costs are represented with :class:`decimal.Decimal` because floating point arithmetic is
unacceptable for billing. All amounts are stored with micro-dollar precision.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ai_gateway.domain.errors import ValidationError

_PRECISION = Decimal("0.000001")
_CURRENCY_CODE_LENGTH = 3


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An immutable monetary amount.

    Attributes:
        amount: The value, quantised to six decimal places.
        currency: ISO-4217 currency code.
    """

    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        """Normalise and validate the amount.

        Raises:
            ValidationError: If the currency code is malformed.
        """
        if len(self.currency) != _CURRENCY_CODE_LENGTH or not self.currency.isalpha():
            raise ValidationError(f"Invalid currency code: {self.currency!r}")
        object.__setattr__(self, "currency", self.currency.upper())
        object.__setattr__(
            self, "amount", Decimal(self.amount).quantize(_PRECISION, rounding=ROUND_HALF_UP)
        )

    @classmethod
    def zero(cls, currency: str = "USD") -> Money:
        """Return a zero amount.

        Args:
            currency: ISO-4217 currency code.

        Returns:
            A ``Money`` instance of value zero.
        """
        return cls(Decimal("0"), currency)

    @classmethod
    def of(cls, amount: str | int | float | Decimal, currency: str = "USD") -> Money:
        """Build a ``Money`` from a loosely typed amount.

        Args:
            amount: Numeric amount; floats are converted via ``str`` to avoid binary drift.
            currency: ISO-4217 currency code.

        Returns:
            A ``Money`` instance.
        """
        return cls(Decimal(str(amount)), currency)

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValidationError(
                f"Cannot combine {self.currency} with {other.currency}",
                details={"left": self.currency, "right": other.currency},
            )

    def __add__(self, other: Money) -> Money:
        """Add two amounts of the same currency."""
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        """Subtract two amounts of the same currency."""
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: int | Decimal) -> Money:
        """Scale the amount by a factor."""
        return Money(self.amount * Decimal(factor), self.currency)

    @property
    def micros(self) -> int:
        """Return the amount expressed in millionths of the currency unit."""
        return int((self.amount * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP))

    @classmethod
    def from_micros(cls, micros: int, currency: str = "USD") -> Money:
        """Build a ``Money`` from an integer micro-unit amount.

        Args:
            micros: Millionths of a currency unit.
            currency: ISO-4217 currency code.

        Returns:
            A ``Money`` instance.
        """
        return cls(Decimal(micros) / Decimal(1_000_000), currency)

    def is_zero(self) -> bool:
        """Return ``True`` when the amount is exactly zero."""
        return self.amount == 0

    def __str__(self) -> str:
        """Return a human readable representation."""
        return f"{self.amount.normalize():f} {self.currency}"


__all__ = ["Money"]
