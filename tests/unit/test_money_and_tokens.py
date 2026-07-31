"""Unit tests for money and token value objects."""

from __future__ import annotations

import pytest

from ai_gateway.domain.errors import ValidationError
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.tokens import TokenUsage


def test_money_arithmetic_and_micros() -> None:
    left = Money.of("0.0015")
    right = Money.of("0.0025")
    assert (left + right).amount == Money.of("0.004").amount
    assert left.micros == 1500
    assert Money.from_micros(1500) == left


def test_money_rejects_currency_mismatch() -> None:
    with pytest.raises(ValidationError):
        _ = Money.of("1", "USD") + Money.of("1", "EUR")


def test_token_usage_totals() -> None:
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, cached_prompt_tokens=2)
    assert usage.total_tokens == 15
    assert usage.billable_prompt_tokens == 8
    assert (usage + usage).prompt_tokens == 20


def test_token_usage_rejects_negatives() -> None:
    with pytest.raises(ValidationError):
        TokenUsage(prompt_tokens=-1)
