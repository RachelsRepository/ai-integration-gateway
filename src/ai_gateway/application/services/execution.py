"""Resilient provider execution.

Wraps every upstream call in the gateway's reliability envelope: circuit breaking,
bounded retries with exponential backoff, per-attempt timeouts and cross-provider
failover. Use cases never talk to a provider adapter directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Generic, TypeVar

from ai_gateway.application.ports.clock import Clock
from ai_gateway.application.ports.llm_provider import (
    EmbeddingsRequest,
    EmbeddingsResponse,
    ProviderCallContext,
    ProviderChatRequest,
    ProviderChatResponse,
    StreamChunk,
)
from ai_gateway.application.ports.metrics import MetricsRecorder
from ai_gateway.application.ports.provider_registry import ProviderRegistry
from ai_gateway.application.ports.resilience import CircuitBreaker, CircuitBreakerRegistry
from ai_gateway.domain.errors import (
    CircuitOpenError,
    DomainError,
    NoProviderAvailableError,
    ProviderTimeoutError,
)
from ai_gateway.domain.policies.retry import RetryPolicy
from ai_gateway.domain.policies.routing import RoutingCandidate
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.provider import ProviderStatus

T = TypeVar("T")

Call = Callable[[RoutingCandidate, int], Awaitable[T]]


@dataclass(frozen=True, slots=True)
class ExecutionOutcome(Generic[T]):
    """The result of a resilient call plus the trace of how it was obtained.

    Attributes:
        value: The successful response.
        model: Model that produced the response.
        attempts: Qualified model references attempted, in order.
        total_attempts: Number of upstream calls issued, including retries.
        fallback_used: Whether a fallback candidate produced the response.
        errors: Failure descriptions keyed by qualified model reference.
        latency_ms: Wall-clock latency of the successful attempt.
    """

    value: T
    model: ModelRef
    attempts: tuple[str, ...]
    total_attempts: int
    fallback_used: bool
    errors: dict[str, str] = field(default_factory=dict)
    latency_ms: int = 0


class ProviderExecutor:
    """Executes provider calls with retries, circuit breaking and failover."""

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        breakers: CircuitBreakerRegistry,
        clock: Clock,
        metrics: MetricsRecorder,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """Initialise the executor.

        Args:
            providers: Registry supplying configured adapters.
            breakers: Circuit breaker registry, one breaker per provider.
            clock: Injected clock used for backoff and latency measurement.
            metrics: Metrics sink.
            retry_policy: Retry policy applied per candidate.
        """
        self._providers = providers
        self._breakers = breakers
        self._clock = clock
        self._metrics = metrics
        self._retry = retry_policy or RetryPolicy()

    # ------------------------------------------------------------------ chat
    async def chat(
        self,
        chain: tuple[RoutingCandidate, ...],
        request: ProviderChatRequest,
        context: ProviderCallContext,
    ) -> ExecutionOutcome[ProviderChatResponse]:
        """Execute a chat completion across a failover chain.

        Args:
            chain: Ordered routing candidates: primary first.
            request: Normalised chat request; its model is replaced per candidate.
            context: Cross-cutting call context.

        Returns:
            The successful response and its execution trace.
        """

        async def call(candidate: RoutingCandidate, attempt: int) -> ProviderChatResponse:
            provider = self._providers.get(candidate.provider)
            scoped = replace(request, model=candidate.ref)
            return await provider.chat(scoped, replace(context, attempt=attempt))

        return await self._execute(chain, context, call, operation="chat")

    async def embed(
        self,
        chain: tuple[RoutingCandidate, ...],
        request: EmbeddingsRequest,
        context: ProviderCallContext,
    ) -> ExecutionOutcome[EmbeddingsResponse]:
        """Execute an embeddings call across a failover chain.

        Args:
            chain: Ordered routing candidates.
            request: Normalised embeddings request.
            context: Cross-cutting call context.

        Returns:
            The successful response and its execution trace.
        """

        async def call(candidate: RoutingCandidate, attempt: int) -> EmbeddingsResponse:
            provider = self._providers.get(candidate.provider)
            scoped = replace(request, model=candidate.ref)
            return await provider.embed(scoped, replace(context, attempt=attempt))

        return await self._execute(chain, context, call, operation="embeddings")

    async def stream_chat(
        self,
        chain: tuple[RoutingCandidate, ...],
        request: ProviderChatRequest,
        context: ProviderCallContext,
    ) -> AsyncIterator[tuple[ModelRef, StreamChunk]]:
        """Stream a chat completion, failing over until the first chunk arrives.

        Once the first token has reached the client the response is committed: a
        mid-stream failure is surfaced as an error rather than silently retried against
        another provider, because partial output cannot be recalled.

        Args:
            chain: Ordered routing candidates.
            request: Normalised chat request.
            context: Cross-cutting call context.

        Yields:
            Tuples of the serving model reference and the incremental chunk.

        Raises:
            NoProviderAvailableError: If the chain is empty or every candidate fails
                before emitting a chunk.
        """
        if not chain:
            raise NoProviderAvailableError("Routing produced no candidates")

        errors: dict[str, str] = {}
        for candidate in chain:
            breaker = self._breakers.get(candidate.provider.value)
            if not await breaker.allows_request():
                errors[candidate.ref.qualified] = "circuit_open"
                continue

            started = self._clock.monotonic()
            emitted = False
            try:
                provider = self._providers.get(candidate.provider)
                scoped = replace(request, model=candidate.ref)
                async with asyncio.timeout(context.timeout_seconds):
                    async for chunk in provider.stream_chat(scoped, context):
                        emitted = True
                        yield candidate.ref, chunk
            except TimeoutError as exc:
                await self._on_failure(candidate, breaker, "timeout", started)
                errors[candidate.ref.qualified] = "timeout"
                if emitted:
                    raise ProviderTimeoutError(
                        "Stream timed out after partial delivery",
                        provider=candidate.provider.value,
                    ) from exc
                continue
            except DomainError as exc:
                await self._on_failure(candidate, breaker, exc.code, started)
                errors[candidate.ref.qualified] = exc.code
                if emitted or not exc.retryable:
                    raise
                continue
            else:
                await self._on_success(candidate, breaker, started)
                return

        raise NoProviderAvailableError(
            "Every candidate failed before the stream started", details={"errors": errors}
        )

    # ------------------------------------------------------------------ internals
    async def _execute(
        self,
        chain: tuple[RoutingCandidate, ...],
        context: ProviderCallContext,
        call: Call[T],
        *,
        operation: str,
    ) -> ExecutionOutcome[T]:
        if not chain:
            raise NoProviderAvailableError("Routing produced no candidates")

        state = _ChainState()
        for position, candidate in enumerate(chain):
            outcome = await self._try_candidate(
                candidate, position, context, call, operation=operation, state=state
            )
            if outcome is not None:
                return outcome

        self._metrics.increment(
            "gateway_provider_calls_total",
            labels={"provider": "chain", "operation": operation, "outcome": "exhausted"},
        )
        raise self._chain_failure(state)

    async def _try_candidate(
        self,
        candidate: RoutingCandidate,
        position: int,
        context: ProviderCallContext,
        call: Call[T],
        *,
        operation: str,
        state: _ChainState,
    ) -> ExecutionOutcome[T] | None:
        qualified = candidate.ref.qualified
        state.attempted.append(qualified)
        breaker = self._breakers.get(candidate.provider.value)

        if not await breaker.allows_request():
            state.errors[qualified] = "circuit_open"
            state.last_error = CircuitOpenError(
                "Circuit is open for provider",
                details={"provider": candidate.provider.value},
            )
            return None

        for attempt in self._retry.attempts():
            state.total_attempts += 1
            started = self._clock.monotonic()
            try:
                async with asyncio.timeout(context.timeout_seconds):
                    value = await call(candidate, attempt)
            except TimeoutError as exc:
                state.last_error = ProviderTimeoutError(
                    f"Provider call exceeded {context.timeout_seconds}s",
                    provider=candidate.provider.value,
                )
                state.errors[qualified] = "timeout"
                await self._on_failure(candidate, breaker, "timeout", started)
                if not self._retry.should_retry(exc, attempt=attempt):
                    return None
                await self._backoff(attempt)
            except DomainError as exc:
                state.last_error = exc
                state.errors[qualified] = exc.code
                await self._on_failure(candidate, breaker, exc.code, started)
                if not self._retry.should_retry(exc, attempt=attempt):
                    return None
                await self._backoff(attempt)
            else:
                latency_ms = await self._on_success(candidate, breaker, started)
                self._metrics.increment(
                    "gateway_provider_calls_total",
                    labels={
                        "provider": candidate.provider.value,
                        "operation": operation,
                        "outcome": "success",
                    },
                )
                return ExecutionOutcome(
                    value=value,
                    model=candidate.ref,
                    attempts=tuple(state.attempted),
                    total_attempts=state.total_attempts,
                    fallback_used=position > 0,
                    errors=dict(state.errors),
                    latency_ms=latency_ms,
                )
        return None

    @staticmethod
    def _chain_failure(state: _ChainState) -> DomainError:
        if isinstance(state.last_error, DomainError):
            state.last_error.details.setdefault("chain_errors", dict(state.errors))
            return state.last_error
        return NoProviderAvailableError(
            "Every routing candidate failed", details={"errors": dict(state.errors)}
        )

    async def _backoff(self, attempt: int) -> None:
        delay = self._retry.delay_for(attempt)
        if delay > 0:
            await self._clock.sleep(delay)

    async def _on_success(
        self, candidate: RoutingCandidate, breaker: CircuitBreaker, started: float
    ) -> int:
        latency_ms = int((self._clock.monotonic() - started) * 1000)
        await breaker.record_success(duration_ms=latency_ms)
        self._providers.record_outcome(candidate.provider, success=True, latency_ms=latency_ms)
        self._providers.record_status(candidate.provider, ProviderStatus.HEALTHY)
        self._metrics.observe(
            "gateway_provider_latency_ms",
            latency_ms,
            labels={"provider": candidate.provider.value, "model": candidate.ref.name},
        )
        return latency_ms

    async def _on_failure(
        self, candidate: RoutingCandidate, breaker: CircuitBreaker, reason: str, started: float
    ) -> None:
        latency_ms = int((self._clock.monotonic() - started) * 1000)
        await breaker.record_failure(error=reason)
        self._providers.record_outcome(candidate.provider, success=False, latency_ms=latency_ms)
        degraded = (
            ProviderStatus.UNAVAILABLE if breaker.snapshot().is_open else ProviderStatus.DEGRADED
        )
        self._providers.record_status(candidate.provider, degraded)
        self._metrics.increment(
            "gateway_provider_errors_total",
            labels={"provider": candidate.provider.value, "reason": reason},
        )


@dataclass(slots=True)
class _ChainState:
    """Mutable bookkeeping shared across candidates in one execution."""

    attempted: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    total_attempts: int = 0
    last_error: Exception | None = None


__all__ = ["ExecutionOutcome", "ProviderExecutor"]
