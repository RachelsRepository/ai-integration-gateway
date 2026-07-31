"""In-memory cache and lock adapters used by tests and local development."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from types import TracebackType


@dataclass(slots=True)
class _Entry:
    """A cache entry with optional absolute expiry."""

    value: bytes
    expires_at: float | None = None

    def alive(self, now: float) -> bool:
        """Return ``True`` when the entry has not expired."""
        return self.expires_at is None or self.expires_at > now


class InMemoryCache:
    """Process-local key/value cache with TTL support."""

    def __init__(self) -> None:
        """Initialise an empty cache."""
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> bytes | None:
        """Fetch a value.

        Args:
            key: Cache key.

        Returns:
            The stored bytes, or ``None`` on a miss.
        """
        async with self._lock:
            entry = self._store.get(key)
            if entry is None or not entry.alive(time.monotonic()):
                self._store.pop(key, None)
                return None
            return entry.value

    async def set(self, key: str, value: bytes, *, ttl_seconds: int | None = None) -> None:
        """Store a value.

        Args:
            key: Cache key.
            value: Payload.
            ttl_seconds: Expiry in seconds.
        """
        expires = None if ttl_seconds is None else time.monotonic() + ttl_seconds
        async with self._lock:
            self._store[key] = _Entry(value=value, expires_at=expires)

    async def delete(self, key: str) -> None:
        """Remove a value.

        Args:
            key: Cache key.
        """
        async with self._lock:
            self._store.pop(key, None)

    async def incr(self, key: str, *, amount: int = 1, ttl_seconds: int | None = None) -> int:
        """Atomically increment a counter.

        Args:
            key: Counter key.
            amount: Increment step.
            ttl_seconds: Expiry applied when the counter is created.

        Returns:
            The counter value after incrementing.
        """
        async with self._lock:
            entry = self._store.get(key)
            now = time.monotonic()
            if entry is None or not entry.alive(now):
                value = amount
                expires = None if ttl_seconds is None else now + ttl_seconds
            else:
                value = int(entry.value.decode("utf-8")) + amount
                expires = entry.expires_at
            self._store[key] = _Entry(value=str(value).encode("utf-8"), expires_at=expires)
            return value

    async def ping(self) -> bool:
        """Report that the in-memory store is always reachable."""
        return True


@dataclass(slots=True)
class InMemoryLock:
    """A process-local mutual exclusion lease."""

    _lock: asyncio.Lock
    _acquired: bool = False

    async def __aenter__(self) -> bool:
        """Acquire the lease."""
        await self._lock.acquire()
        self._acquired = True
        return True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the lease."""
        if self._acquired:
            self._lock.release()
            self._acquired = False


class InMemoryLockManager:
    """Creates process-local locks."""

    def __init__(self) -> None:
        """Initialise the manager."""
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, name: str, *, ttl_seconds: int = 30, wait_seconds: float = 0.0) -> InMemoryLock:
        """Build a lock over a named resource.

        Args:
            name: Resource name.
            ttl_seconds: Unused for in-memory locks; accepted for interface parity.
            wait_seconds: Unused for in-memory locks.

        Returns:
            An unacquired lock.
        """
        del ttl_seconds, wait_seconds
        lock = self._locks.setdefault(name, asyncio.Lock())
        return InMemoryLock(_lock=lock)


__all__ = ["InMemoryCache", "InMemoryLock", "InMemoryLockManager"]
