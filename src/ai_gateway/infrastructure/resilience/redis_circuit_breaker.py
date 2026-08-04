"""Redis-backed distributed circuit breakers shared across API and worker replicas."""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from ai_gateway.application.ports.resilience import CircuitSnapshot, CircuitState
from ai_gateway.infrastructure.resilience.circuit_breaker import InMemoryCircuitBreaker

_TRANSITION_SCRIPT = """
local key = KEYS[1]
local action = ARGV[1]
local now = tonumber(ARGV[2])
local failure_threshold = tonumber(ARGV[3])
local success_threshold = tonumber(ARGV[4])
local reset_timeout = tonumber(ARGV[5])

local state = redis.call('HGET', key, 'state') or 'closed'
local failures = tonumber(redis.call('HGET', key, 'failures') or '0')
local successes = tonumber(redis.call('HGET', key, 'successes') or '0')
local opened_at = tonumber(redis.call('HGET', key, 'opened_at') or '0')

if action == 'allows' then
  if state == 'closed' then
    return {state, failures, successes, opened_at, 1}
  end
  if state == 'open' then
    if opened_at > 0 and (now - opened_at) >= reset_timeout then
      state = 'half_open'
      successes = 0
      redis.call('HSET', key, 'state', state, 'successes', successes)
      return {state, failures, successes, opened_at, 1}
    end
    return {state, failures, successes, opened_at, 0}
  end
  return {state, failures, successes, opened_at, 1}
end

if action == 'success' then
  if state == 'half_open' then
    successes = successes + 1
    if successes >= success_threshold then
      state = 'closed'
      failures = 0
      successes = 0
      opened_at = 0
    end
  else
    failures = 0
  end
elseif action == 'failure' then
  if state == 'half_open' then
    state = 'open'
    opened_at = now
    successes = 0
    failures = failure_threshold
  else
    failures = failures + 1
    if failures >= failure_threshold then
      state = 'open'
      opened_at = now
      successes = 0
    end
  end
end

redis.call('HSET', key, 'state', state, 'failures', failures,
  'successes', successes, 'opened_at', opened_at)
redis.call('EXPIRE', key, math.max(math.floor(reset_timeout * 4), 120))
return {state, failures, successes, opened_at, 1}
"""

_SCRIPT_RESULT_LEN = 5


@runtime_checkable
class CircuitBreakerRedisClient(Protocol):
    """Minimal async Redis surface used by distributed circuit breakers."""

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | bytes | int | float,
    ) -> object:
        """Evaluate a Lua script against Redis."""

    async def hgetall(self, name: str | bytes) -> dict[bytes, bytes]:
        """Return all fields in a hash key."""


def _as_int(value: object, *, field: str) -> int:
    """Parse Redis script numeric fields without treating arbitrary objects as int."""
    if isinstance(value, bool):
        raise TypeError(f"circuit field {field} must be numeric, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (str, bytes, bytearray)):
        return int(value)
    raise TypeError(f"circuit field {field} must be int-compatible, got {type(value).__name__}")


def _as_float(value: object, *, field: str) -> float:
    """Parse Redis script float fields without treating arbitrary objects as float."""
    if isinstance(value, bool):
        raise TypeError(f"circuit field {field} must be numeric, got bool")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (str, bytes, bytearray)):
        return float(value)
    raise TypeError(f"circuit field {field} must be float-compatible, got {type(value).__name__}")


class RedisCircuitBreaker:
    """Circuit breaker whose state lives in Redis."""

    def __init__(
        self,
        client: CircuitBreakerRedisClient,
        name: str,
        *,
        failure_threshold: int,
        success_threshold: int,
        reset_timeout_seconds: float,
        window_size: int,
    ) -> None:
        """Initialise a Redis-backed breaker."""
        self.name = name
        self._client = client
        self._failure_threshold = failure_threshold
        self._success_threshold = success_threshold
        self._reset_timeout_seconds = reset_timeout_seconds
        self._window_size = window_size
        self._key = f"aigw:cb:{name}"
        self._local = InMemoryCircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            success_threshold=success_threshold,
            reset_timeout_seconds=reset_timeout_seconds,
            window_size=window_size,
        )

    async def _eval(self, action: str) -> tuple[CircuitState, int, int, float | None, bool]:
        try:
            result = await self._client.eval(
                _TRANSITION_SCRIPT,
                1,
                self._key,
                action,
                str(time.time()),
                str(self._failure_threshold),
                str(self._success_threshold),
                str(self._reset_timeout_seconds),
            )
        except Exception:
            # Fail open for routing probes only when Redis is down: still use local state.
            if action == "allows":
                allowed = await self._local.allows_request()
                snap = self._local.snapshot()
                return (
                    snap.state,
                    snap.failure_count,
                    snap.success_count,
                    snap.opened_at_monotonic,
                    allowed,
                )
            if action == "success":
                await self._local.record_success()
            else:
                await self._local.record_failure()
            snap = self._local.snapshot()
            return (
                snap.state,
                snap.failure_count,
                snap.success_count,
                snap.opened_at_monotonic,
                True,
            )

        if not isinstance(result, (list, tuple)) or len(result) < _SCRIPT_RESULT_LEN:
            raise TypeError(f"unexpected circuit script result: {result!r}")
        state_raw = result[0]
        if isinstance(state_raw, bytes):
            state_raw = state_raw.decode()
        state = CircuitState(str(state_raw))
        failures = _as_int(result[1], field="failures")
        successes = _as_int(result[2], field="successes")
        opened_raw = _as_float(result[3], field="opened_at")
        opened_at = opened_raw if opened_raw > 0 else None
        allowed = bool(_as_int(result[4], field="allowed"))
        return state, failures, successes, opened_at, allowed

    async def allows_request(self) -> bool:
        """Report whether a call may proceed."""
        _state, _f, _s, _o, allowed = await self._eval("allows")
        return allowed

    async def record_success(self, *, duration_ms: int = 0) -> None:
        """Record a successful call."""
        del duration_ms
        await self._eval("success")

    async def record_failure(self, *, error: str = "") -> None:
        """Record a failed call."""
        del error
        await self._eval("failure")

    def snapshot(self) -> CircuitSnapshot:
        """Best-effort local view; prefer async refresh for accuracy."""
        return self._local.snapshot()

    async def refresh_snapshot(self) -> CircuitSnapshot:
        """Load the shared Redis state into a snapshot."""
        try:
            data = await self._client.hgetall(self._key)
        except Exception:
            return self._local.snapshot()
        if not data:
            return CircuitSnapshot(name=self.name, state=CircuitState.CLOSED)
        decode = {
            (k.decode() if isinstance(k, bytes) else str(k)): (
                v.decode() if isinstance(v, bytes) else str(v)
            )
            for k, v in data.items()
        }
        opened_raw = float(decode.get("opened_at", "0") or 0)
        return CircuitSnapshot(
            name=self.name,
            state=CircuitState(decode.get("state", "closed")),
            failure_count=int(decode.get("failures", "0") or 0),
            success_count=int(decode.get("successes", "0") or 0),
            opened_at_monotonic=opened_raw if opened_raw > 0 else None,
        )


class RedisCircuitBreakerRegistry:
    """Owns one Redis-backed breaker per dependency name."""

    def __init__(
        self,
        client: CircuitBreakerRedisClient,
        *,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        reset_timeout_seconds: float = 30.0,
        window_size: int = 50,
    ) -> None:
        """Initialise the Redis circuit registry."""
        self._client = client
        self._failure_threshold = failure_threshold
        self._success_threshold = success_threshold
        self._reset_timeout_seconds = reset_timeout_seconds
        self._window_size = window_size
        self._breakers: dict[str, RedisCircuitBreaker] = {}

    def get(self, name: str) -> RedisCircuitBreaker:
        """Fetch or lazily create a breaker."""
        breaker = self._breakers.get(name)
        if breaker is None:
            breaker = RedisCircuitBreaker(
                self._client,
                name,
                failure_threshold=self._failure_threshold,
                success_threshold=self._success_threshold,
                reset_timeout_seconds=self._reset_timeout_seconds,
                window_size=self._window_size,
            )
            self._breakers[name] = breaker
        return breaker

    def snapshots(self) -> dict[str, CircuitSnapshot]:
        """Return cached breaker names with best-effort local snapshots."""
        return {name: breaker.snapshot() for name, breaker in self._breakers.items()}


__all__ = [
    "CircuitBreakerRedisClient",
    "RedisCircuitBreaker",
    "RedisCircuitBreakerRegistry",
]
