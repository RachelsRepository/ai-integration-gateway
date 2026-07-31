"""System clock adapter."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime


class SystemClock:
    """Clock backed by the host operating system."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Return a monotonic timestamp in seconds."""
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Suspend the caller for a duration.

        Args:
            seconds: Duration to sleep.
        """
        await asyncio.sleep(seconds)


__all__ = ["SystemClock"]
