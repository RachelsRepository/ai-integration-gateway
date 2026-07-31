"""Resilience ports: circuit breaking and health observation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class CircuitState(StrEnum):
    """States of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    """Observable state of a circuit breaker.

    Attributes:
        name: Breaker name, typically the provider identifier.
        state: Current state.
        failure_count: Consecutive failures recorded in the current window.
        success_count: Consecutive successes recorded in half-open state.
        opened_at_monotonic: Monotonic timestamp at which the breaker opened.
        error_rate: Rolling error rate in ``[0, 1]``.
    """

    name: str
    state: CircuitState
    failure_count: int = 0
    success_count: int = 0
    opened_at_monotonic: float | None = None
    error_rate: float = 0.0

    @property
    def is_open(self) -> bool:
        """Return ``True`` when calls are currently being short-circuited."""
        return self.state is CircuitState.OPEN


@runtime_checkable
class CircuitBreaker(Protocol):
    """Guards a dependency against cascading failure."""

    @property
    def name(self) -> str:
        """Return the breaker name."""
        ...

    async def allows_request(self) -> bool:
        """Report whether a call may proceed right now."""
        ...

    async def record_success(self, *, duration_ms: int = 0) -> None:
        """Record a successful call.

        Args:
            duration_ms: Observed latency.
        """
        ...

    async def record_failure(self, *, error: str = "") -> None:
        """Record a failed call.

        Args:
            error: Short failure description.
        """
        ...

    def snapshot(self) -> CircuitSnapshot:
        """Return the breaker's current observable state."""
        ...


@runtime_checkable
class CircuitBreakerRegistry(Protocol):
    """Owns one circuit breaker per guarded dependency."""

    def get(self, name: str) -> CircuitBreaker:
        """Fetch or lazily create a breaker.

        Args:
            name: Breaker name.

        Returns:
            The breaker.
        """
        ...

    def snapshots(self) -> dict[str, CircuitSnapshot]:
        """Return the state of every known breaker."""
        ...


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitSnapshot",
    "CircuitState",
]
