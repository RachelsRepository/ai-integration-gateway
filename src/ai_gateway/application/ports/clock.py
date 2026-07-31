"""Clock port.

Injecting time keeps quota windows, retention jobs and backoff schedules deterministic
under test.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Supplies the current time and monotonic durations."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...

    def monotonic(self) -> float:
        """Return a monotonic timestamp in seconds, suitable for measuring latency."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Suspend the caller.

        Args:
            seconds: Duration to sleep.
        """
        ...


__all__ = ["Clock"]
