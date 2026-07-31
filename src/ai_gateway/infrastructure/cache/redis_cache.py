"""Redis-backed cache and distributed locks."""

from __future__ import annotations

import asyncio
import time
import uuid
from types import TracebackType
from typing import Any

from redis.asyncio import Redis

from ai_gateway.domain.errors import DomainError

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


class RedisCache:
    """Cache adapter over Redis."""

    def __init__(self, client: Redis) -> None:  # type: ignore[type-arg]
        """Initialise the cache.

        Args:
            client: Connected Redis client.
        """
        self._client = client

    async def get(self, key: str) -> bytes | None:
        """Fetch a value.

        Args:
            key: Cache key.

        Returns:
            The stored bytes, or ``None`` on a miss.
        """
        value: bytes | None = await self._client.get(key)
        return value

    async def set(self, key: str, value: bytes, *, ttl_seconds: int | None = None) -> None:
        """Store a value.

        Args:
            key: Cache key.
            value: Payload.
            ttl_seconds: Expiry in seconds.
        """
        if ttl_seconds is None:
            await self._client.set(key, value)
        else:
            await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        """Remove a value.

        Args:
            key: Cache key.
        """
        await self._client.delete(key)

    async def incr(self, key: str, *, amount: int = 1, ttl_seconds: int | None = None) -> int:
        """Atomically increment a counter.

        Args:
            key: Counter key.
            amount: Increment step.
            ttl_seconds: Expiry applied when the counter is created.

        Returns:
            The counter value after incrementing.
        """
        value = await self._client.incrby(key, amount)
        if value == amount and ttl_seconds is not None:
            await self._client.expire(key, ttl_seconds)
        return int(value)

    async def ping(self) -> bool:
        """Report whether Redis is reachable."""
        try:
            return bool(await self._client.ping())
        except Exception:
            return False


class RedisLock:
    """A Redis-backed mutual exclusion lease."""

    def __init__(
        self,
        client: Redis,  # type: ignore[type-arg]
        name: str,
        *,
        ttl_seconds: int,
        wait_seconds: float,
    ) -> None:
        """Initialise the lock.

        Args:
            client: Connected Redis client.
            name: Resource name.
            ttl_seconds: Lease duration.
            wait_seconds: How long to block waiting for the lease.
        """
        self._client = client
        self._name = f"aigw:lock:{name}"
        self._ttl = ttl_seconds
        self._wait = wait_seconds
        self._token = str(uuid.uuid4())
        self._acquired = False

    async def __aenter__(self) -> bool:
        """Attempt to acquire the lease."""
        deadline = time.monotonic() + self._wait
        while True:
            acquired = await self._client.set(self._name, self._token, nx=True, ex=self._ttl)
            if acquired:
                self._acquired = True
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the lease if held."""
        if self._acquired:
            await self._client.eval(  # type: ignore[no-untyped-call]
                _RELEASE_SCRIPT, 1, self._name, self._token
            )
            self._acquired = False


class RedisLockManager:
    """Creates Redis-backed distributed locks."""

    def __init__(self, client: Redis) -> None:  # type: ignore[type-arg]
        """Initialise the manager.

        Args:
            client: Connected Redis client.
        """
        self._client = client

    def lock(self, name: str, *, ttl_seconds: int = 30, wait_seconds: float = 0.0) -> RedisLock:
        """Build a lock over a named resource.

        Args:
            name: Resource name.
            ttl_seconds: Lease duration.
            wait_seconds: How long to block waiting for the lease.

        Returns:
            An unacquired lock.
        """
        return RedisLock(self._client, name, ttl_seconds=ttl_seconds, wait_seconds=wait_seconds)


def create_redis_client(url: str, **kwargs: Any) -> Redis[bytes]:
    """Create a Redis client from a URL.

    Args:
        url: Redis connection URL.
        **kwargs: Additional client options.

    Returns:
        The client.
    """
    try:
        return Redis.from_url(url, decode_responses=False, **kwargs)
    except Exception as exc:  # pragma: no cover - construction failure
        raise DomainError("Failed to create Redis client", details={"error": str(exc)}) from exc


__all__ = ["RedisCache", "RedisLock", "RedisLockManager", "create_redis_client"]
