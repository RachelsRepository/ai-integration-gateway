"""Rate limiting port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ai_gateway.domain.errors import RateLimitExceededError


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """The outcome of consuming from a rate limit bucket.

    Attributes:
        allowed: Whether the request may proceed.
        limit: Configured sustained capacity.
        remaining: Tokens left in the bucket after this decision.
        retry_after_seconds: Suggested backoff when denied.
    """

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int = 0

    def raise_if_denied(self) -> None:
        """Raise when the request was throttled.

        Raises:
            RateLimitExceededError: If ``allowed`` is ``False``.
        """
        if not self.allowed:
            raise RateLimitExceededError(
                "Request rate limit exceeded",
                retry_after_seconds=max(self.retry_after_seconds, 1),
                details={"limit": self.limit, "remaining": self.remaining},
            )


@runtime_checkable
class RateLimiter(Protocol):
    """Enforces a per-key request rate."""

    async def acquire(
        self, key: str, *, limit_per_minute: int, burst: int = 0, cost: int = 1
    ) -> RateLimitDecision:
        """Attempt to consume capacity for a key.

        Args:
            key: Bucket key, typically ``tenant:{id}`` or ``apikey:{id}``.
            limit_per_minute: Sustained refill rate.
            burst: Additional capacity above the sustained rate.
            cost: Tokens consumed by this request.

        Returns:
            The decision, including remaining capacity.
        """
        ...


__all__ = ["RateLimitDecision", "RateLimiter"]
