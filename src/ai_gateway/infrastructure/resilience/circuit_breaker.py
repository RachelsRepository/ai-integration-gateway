"""In-memory circuit breaker.

A closed breaker records outcomes into a sliding window. When the failure rate exceeds the
threshold the breaker opens, short-circuiting subsequent calls until the reset timeout
elapses, at which point it admits a probe (half-open). One successful probe closes it;
one failed probe re-opens it.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from ai_gateway.application.ports.resilience import CircuitSnapshot, CircuitState


@dataclass(slots=True)
class InMemoryCircuitBreaker:
    """Process-local circuit breaker.

    Attributes:
        name: Breaker name.
        failure_threshold: Consecutive failures that open the breaker.
        success_threshold: Consecutive half-open successes that close it.
        reset_timeout_seconds: How long the breaker stays open.
        window_size: Sliding window used for the error-rate gauge.
    """

    name: str
    failure_threshold: int = 5
    success_threshold: int = 2
    reset_timeout_seconds: float = 30.0
    window_size: int = 50
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _successes: int = 0
    _opened_at: float | None = None
    _window: deque[bool] = field(default_factory=deque)

    async def allows_request(self) -> bool:
        """Report whether a call may proceed."""
        if self._state is CircuitState.CLOSED:
            return True
        if self._state is CircuitState.OPEN:
            if (
                self._opened_at is not None
                and time.monotonic() - self._opened_at >= self.reset_timeout_seconds
            ):
                self._state = CircuitState.HALF_OPEN
                self._successes = 0
                return True
            return False
        return True

    async def record_success(self, *, duration_ms: int = 0) -> None:
        """Record a successful call.

        Args:
            duration_ms: Observed latency; unused by the state machine.
        """
        del duration_ms
        self._push(True)
        if self._state is CircuitState.HALF_OPEN:
            self._successes += 1
            if self._successes >= self.success_threshold:
                self._close()
            return
        self._failures = 0

    async def record_failure(self, *, error: str = "") -> None:
        """Record a failed call.

        Args:
            error: Short failure description; unused by the state machine.
        """
        del error
        self._push(False)
        if self._state is CircuitState.HALF_OPEN:
            self._open()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open()

    def snapshot(self) -> CircuitSnapshot:
        """Return the breaker's observable state."""
        return CircuitSnapshot(
            name=self.name,
            state=self._state,
            failure_count=self._failures,
            success_count=self._successes,
            opened_at_monotonic=self._opened_at,
            error_rate=self._error_rate(),
        )

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._successes = 0

    def _close(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = None

    def _push(self, success: bool) -> None:
        self._window.append(success)
        while len(self._window) > self.window_size:
            self._window.popleft()

    def _error_rate(self) -> float:
        if not self._window:
            return 0.0
        failures = sum(1 for ok in self._window if not ok)
        return failures / len(self._window)


class InMemoryCircuitBreakerRegistry:
    """Owns one in-memory breaker per dependency name."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        reset_timeout_seconds: float = 30.0,
        window_size: int = 50,
    ) -> None:
        """Initialise the registry.

        Args:
            failure_threshold: Default failure threshold.
            success_threshold: Default half-open success threshold.
            reset_timeout_seconds: Default reset timeout.
            window_size: Default sliding window size.
        """
        self._failure_threshold = failure_threshold
        self._success_threshold = success_threshold
        self._reset_timeout_seconds = reset_timeout_seconds
        self._window_size = window_size
        self._breakers: dict[str, InMemoryCircuitBreaker] = {}

    def get(self, name: str) -> InMemoryCircuitBreaker:
        """Fetch or lazily create a breaker.

        Args:
            name: Breaker name.

        Returns:
            The breaker.
        """
        breaker = self._breakers.get(name)
        if breaker is None:
            breaker = InMemoryCircuitBreaker(
                name=name,
                failure_threshold=self._failure_threshold,
                success_threshold=self._success_threshold,
                reset_timeout_seconds=self._reset_timeout_seconds,
                window_size=self._window_size,
            )
            self._breakers[name] = breaker
        return breaker

    def snapshots(self) -> dict[str, CircuitSnapshot]:
        """Return the state of every known breaker."""
        return {name: breaker.snapshot() for name, breaker in self._breakers.items()}


__all__ = ["InMemoryCircuitBreaker", "InMemoryCircuitBreakerRegistry"]
