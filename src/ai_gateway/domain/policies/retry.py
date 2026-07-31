"""Retry and backoff policy.

Backoff maths is pure and therefore lives in the domain, where it can be tested without a
clock or an event loop.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from ai_gateway.domain.errors import DomainError, ValidationError

Jitter = Callable[[], float]


def _default_jitter() -> float:
    """Return a uniform random multiplier in ``[0, 1)``.

    Returns:
        A pseudo-random float. Retry jitter is not a security control, so the default
        pseudo-random source is appropriate.
    """
    return random.random()  # noqa: S311 - jitter is not a security control


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Full-jitter exponential backoff with a bounded attempt budget.

    Attributes:
        max_attempts: Total attempts including the first call.
        base_delay_seconds: Delay after the first failure.
        max_delay_seconds: Ceiling applied to every computed delay.
        multiplier: Exponential growth factor.
        jitter: Whether to apply full jitter to the computed delay.
        retry_on_timeout: Whether timeouts are considered retryable.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 8.0
    multiplier: float = 2.0
    jitter: bool = True
    retry_on_timeout: bool = True

    def __post_init__(self) -> None:
        """Validate the policy.

        Raises:
            ValidationError: If the attempt budget or delays are not positive.
        """
        if self.max_attempts < 1:
            raise ValidationError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValidationError("Retry delays must not be negative")
        if self.multiplier < 1:
            raise ValidationError("Retry multiplier must be at least 1")

    def delay_for(self, attempt: int, *, jitter_source: Jitter | None = None) -> float:
        """Compute the delay to apply before a given attempt.

        Args:
            attempt: One-based number of the attempt that just failed.
            jitter_source: Injected randomness, defaulting to the module source.

        Returns:
            The delay in seconds before the next attempt.
        """
        if attempt < 1:
            return 0.0
        raw = self.base_delay_seconds * (self.multiplier ** (attempt - 1))
        capped = min(raw, self.max_delay_seconds)
        if not self.jitter:
            return capped
        source = jitter_source or _default_jitter
        return capped * source()

    def should_retry(self, error: Exception, *, attempt: int) -> bool:
        """Decide whether another attempt is warranted.

        Args:
            error: The failure that occurred.
            attempt: One-based number of the attempt that just failed.

        Returns:
            ``True`` when the caller should retry.
        """
        if attempt >= self.max_attempts:
            return False
        if isinstance(error, TimeoutError):
            return self.retry_on_timeout
        if isinstance(error, DomainError):
            return error.retryable
        return False

    def attempts(self) -> range:
        """Return the one-based range of attempt numbers permitted by the policy."""
        return range(1, self.max_attempts + 1)


__all__ = ["Jitter", "RetryPolicy"]
