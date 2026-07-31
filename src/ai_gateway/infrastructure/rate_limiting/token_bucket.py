"""Token-bucket rate limiter backed by the Cache port.

Uses a fixed-window counter for simplicity and correctness under Redis. The sustained
rate is ``limit_per_minute`` and the burst is added as additional capacity within the
current minute window.
"""

from __future__ import annotations

import math
import time

from ai_gateway.application.ports.cache import Cache
from ai_gateway.application.ports.rate_limiter import RateLimitDecision


class TokenBucketRateLimiter:
    """Rate limiter that stores counters in a Cache."""

    def __init__(self, cache: Cache) -> None:
        """Initialise the limiter.

        Args:
            cache: Backing key/value store.
        """
        self._cache = cache

    async def acquire(
        self, key: str, *, limit_per_minute: int, burst: int = 0, cost: int = 1
    ) -> RateLimitDecision:
        """Attempt to consume capacity for a key.

        Args:
            key: Bucket key.
            limit_per_minute: Sustained refill rate.
            burst: Additional capacity above the sustained rate.
            cost: Tokens consumed by this request.

        Returns:
            The decision, including remaining capacity.
        """
        capacity = max(limit_per_minute + burst, 1)
        window = int(time.time() // 60)
        redis_key = f"aigw:rl:{key}:{window}"
        used = await self._cache.incr(redis_key, amount=cost, ttl_seconds=120)
        remaining = max(capacity - used, 0)
        allowed = used <= capacity
        retry_after = 0 if allowed else max(int(60 - (time.time() % 60)), 1)
        if not allowed:
            # Best-effort compensation so a denied request does not permanently burn budget.
            await self._cache.incr(redis_key, amount=-cost, ttl_seconds=120)
            remaining = max(capacity - (used - cost), 0)
            retry_after = max(math.ceil(60 - (time.time() % 60)), 1)
        return RateLimitDecision(
            allowed=allowed,
            limit=capacity,
            remaining=remaining,
            retry_after_seconds=retry_after,
        )


__all__ = ["TokenBucketRateLimiter"]
