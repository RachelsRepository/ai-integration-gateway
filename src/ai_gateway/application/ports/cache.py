"""Cache and distributed lock ports."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, runtime_checkable


@runtime_checkable
class Cache(Protocol):
    """A namespaced key/value cache with expiry."""

    async def get(self, key: str) -> bytes | None:
        """Fetch a value.

        Args:
            key: Cache key.

        Returns:
            The stored bytes, or ``None`` on a miss.
        """
        ...

    async def set(self, key: str, value: bytes, *, ttl_seconds: int | None = None) -> None:
        """Store a value.

        Args:
            key: Cache key.
            value: Payload to store.
            ttl_seconds: Expiry in seconds; no expiry when omitted.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove a value.

        Args:
            key: Cache key.
        """
        ...

    async def incr(self, key: str, *, amount: int = 1, ttl_seconds: int | None = None) -> int:
        """Atomically increment a counter.

        Args:
            key: Counter key.
            amount: Increment step.
            ttl_seconds: Expiry applied when the counter is created.

        Returns:
            The counter value after incrementing.
        """
        ...

    async def ping(self) -> bool:
        """Report whether the backing store is reachable."""
        ...


@runtime_checkable
class DistributedLock(Protocol):
    """A mutually exclusive lease over a named resource."""

    async def __aenter__(self) -> bool:
        """Attempt to acquire the lease.

        Returns:
            ``True`` when the lease was acquired.
        """
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the lease if it is still held."""
        ...


@runtime_checkable
class LockManager(Protocol):
    """Creates distributed locks."""

    def lock(
        self, name: str, *, ttl_seconds: int = 30, wait_seconds: float = 0.0
    ) -> DistributedLock:
        """Build a lock over a named resource.

        Args:
            name: Resource name.
            ttl_seconds: Lease duration, after which the lock self-expires.
            wait_seconds: How long to block waiting for the lease.

        Returns:
            An unacquired lock.
        """
        ...


__all__ = ["Cache", "DistributedLock", "LockManager"]
