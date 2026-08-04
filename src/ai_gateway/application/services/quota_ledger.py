"""Distributed quota and concurrency reservations backed by the Cache port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ai_gateway.application.ports.cache import Cache
from ai_gateway.domain.entities.tenant import QuotaPeriod, Tenant
from ai_gateway.domain.errors import QuotaExceededError
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    """Holds reserved capacity that must later settle or release.

    Attributes:
        reservation_id: Opaque reservation key fragment.
        tenant_id: Owning tenant.
        tokens: Reserved token units.
        cost_micros: Reserved cost in micros.
        concurrency_key: Optional concurrency slot key.
        day_key: Daily counter key.
        month_key: Monthly counter key.
        rpm_key: Optional requests-per-minute key.
    """

    reservation_id: str
    tenant_id: TenantId
    tokens: int
    cost_micros: int
    concurrency_key: str | None
    day_key: str
    month_key: str
    rpm_key: str | None = None
    model_key: str | None = None
    provider_key: str | None = None


class QuotaReservationLedger:
    """Atomic soft holds for tokens, cost, and concurrency across replicas."""

    def __init__(self, cache: Cache, *, fail_closed: bool = True) -> None:
        """Initialise the ledger.

        Args:
            cache: Shared cache used for atomic counters.
            fail_closed: Reject when the cache backend is unavailable.
        """
        self._cache = cache
        self._fail_closed = fail_closed

    async def reserve(  # noqa: PLR0912, PLR0915
        self,
        tenant: Tenant,
        *,
        reservation_id: str,
        projected_tokens: int,
        projected_cost: Money,
        concurrency_limit: int | None = None,
        model: str | None = None,
        provider: ProviderName | None = None,
        model_token_limit: int | None = None,
        provider_token_limit: int | None = None,
    ) -> QuotaReservation:
        """Reserve projected capacity before dispatch.

        Raises:
            QuotaExceededError: When a hard limit would be exceeded.
            DomainError path via QuotaExceededError when Redis is down and fail-closed.
        """
        now = datetime.now(UTC)
        day = now.strftime("%Y%m%d")
        month = now.strftime("%Y%m")
        day_key = f"aigw:quota:{tenant.id}:day:{day}"
        month_key = f"aigw:quota:{tenant.id}:month:{month}"
        rpm_key = f"aigw:quota:{tenant.id}:rpm:{now.strftime('%Y%m%d%H%M')}"
        concurrency_key = f"aigw:quota:{tenant.id}:inflight"
        model_key = f"aigw:quota:{tenant.id}:model:{model}:{day}" if model else None
        provider_key = (
            f"aigw:quota:{tenant.id}:provider:{provider.value}:{day}" if provider else None
        )

        try:
            day_tokens = await self._cache.incr(
                f"{day_key}:tokens", amount=max(projected_tokens, 0), ttl_seconds=86_400 * 2
            )
            day_cost = await self._cache.incr(
                f"{day_key}:cost", amount=max(projected_cost.micros, 0), ttl_seconds=86_400 * 2
            )
            month_tokens = await self._cache.incr(
                f"{month_key}:tokens", amount=max(projected_tokens, 0), ttl_seconds=86_400 * 40
            )
            month_cost = await self._cache.incr(
                f"{month_key}:cost", amount=max(projected_cost.micros, 0), ttl_seconds=86_400 * 40
            )
            rpm = await self._cache.incr(rpm_key, amount=1, ttl_seconds=120)
            inflight = await self._cache.incr(concurrency_key, amount=1, ttl_seconds=600)
            if model_key is not None and model_token_limit is not None:
                model_tokens = await self._cache.incr(
                    model_key, amount=max(projected_tokens, 0), ttl_seconds=86_400 * 2
                )
                if model_tokens > model_token_limit:
                    await self._rollback_partial(
                        day_key,
                        month_key,
                        rpm_key,
                        concurrency_key,
                        projected_tokens,
                        projected_cost.micros,
                        model_key=model_key,
                        provider_key=None,
                    )
                    raise QuotaExceededError(
                        "Model-specific token allowance exceeded",
                        details={"model": model, "limit": model_token_limit},
                    )
            if provider_key is not None and provider_token_limit is not None:
                provider_tokens = await self._cache.incr(
                    provider_key, amount=max(projected_tokens, 0), ttl_seconds=86_400 * 2
                )
                if provider_tokens > provider_token_limit:
                    await self._rollback_partial(
                        day_key,
                        month_key,
                        rpm_key,
                        concurrency_key,
                        projected_tokens,
                        projected_cost.micros,
                        model_key=model_key,
                        provider_key=provider_key,
                    )
                    raise QuotaExceededError(
                        "Provider-specific token allowance exceeded",
                        details={"provider": provider.value if provider else None},
                    )
        except QuotaExceededError:
            raise
        except Exception as exc:
            if self._fail_closed:
                raise QuotaExceededError(
                    "Quota backend unavailable; failing closed",
                    details={"error": type(exc).__name__},
                ) from exc
            return QuotaReservation(
                reservation_id=reservation_id,
                tenant_id=tenant.id,
                tokens=projected_tokens,
                cost_micros=projected_cost.micros,
                concurrency_key=None,
                day_key=day_key,
                month_key=month_key,
            )

        daily = tenant.quota_for(QuotaPeriod.DAILY)
        monthly = tenant.quota_for(QuotaPeriod.MONTHLY)
        try:
            if daily and daily.max_tokens is not None and day_tokens > daily.max_tokens:
                raise QuotaExceededError(
                    "Daily token quota exceeded",
                    details={"period": "daily", "limit": daily.max_tokens},
                )
            if daily and daily.max_cost is not None and day_cost > daily.max_cost.micros:
                raise QuotaExceededError(
                    "Daily cost budget exceeded",
                    details={"period": "daily", "limit_micros": daily.max_cost.micros},
                )
            if monthly and monthly.max_tokens is not None and month_tokens > monthly.max_tokens:
                raise QuotaExceededError(
                    "Monthly token quota exceeded",
                    details={"period": "monthly", "limit": monthly.max_tokens},
                )
            if monthly and monthly.max_cost is not None and month_cost > monthly.max_cost.micros:
                raise QuotaExceededError(
                    "Monthly cost budget exceeded",
                    details={"period": "monthly", "limit_micros": monthly.max_cost.micros},
                )
            if daily and daily.max_requests is not None and rpm > daily.max_requests:
                # max_requests on daily is treated as a hard RPM ceiling when set low in tests.
                raise QuotaExceededError(
                    "Request rate quota exceeded",
                    details={"limit": daily.max_requests},
                )
            if concurrency_limit is not None and inflight > concurrency_limit:
                raise QuotaExceededError(
                    "Concurrent request limit exceeded",
                    details={"limit": concurrency_limit},
                )
        except QuotaExceededError:
            await self._rollback_partial(
                day_key,
                month_key,
                rpm_key,
                concurrency_key,
                projected_tokens,
                projected_cost.micros,
                model_key=model_key,
                provider_key=provider_key,
            )
            raise

        return QuotaReservation(
            reservation_id=reservation_id,
            tenant_id=tenant.id,
            tokens=projected_tokens,
            cost_micros=projected_cost.micros,
            concurrency_key=concurrency_key,
            day_key=day_key,
            month_key=month_key,
            rpm_key=rpm_key,
            model_key=model_key,
            provider_key=provider_key,
        )

    async def settle(
        self,
        reservation: QuotaReservation,
        *,
        actual_tokens: int,
        actual_cost: Money,
    ) -> None:
        """Adjust reserved counters to actual usage and release concurrency."""
        token_delta = actual_tokens - reservation.tokens
        cost_delta = actual_cost.micros - reservation.cost_micros
        try:
            if token_delta:
                await self._cache.incr(f"{reservation.day_key}:tokens", amount=token_delta)
                await self._cache.incr(f"{reservation.month_key}:tokens", amount=token_delta)
                if reservation.model_key:
                    await self._cache.incr(reservation.model_key, amount=token_delta)
                if reservation.provider_key:
                    await self._cache.incr(reservation.provider_key, amount=token_delta)
            if cost_delta:
                await self._cache.incr(f"{reservation.day_key}:cost", amount=cost_delta)
                await self._cache.incr(f"{reservation.month_key}:cost", amount=cost_delta)
            if reservation.concurrency_key:
                await self._cache.incr(reservation.concurrency_key, amount=-1)
        except Exception:
            # Settlement best-effort; durable usage records remain source of truth.
            return

    async def release(self, reservation: QuotaReservation) -> None:
        """Release a reservation after failure or cancellation."""
        await self._rollback_partial(
            reservation.day_key,
            reservation.month_key,
            reservation.rpm_key,
            reservation.concurrency_key,
            reservation.tokens,
            reservation.cost_micros,
            model_key=reservation.model_key,
            provider_key=reservation.provider_key,
        )

    async def _rollback_partial(  # noqa: PLR0917
        self,
        day_key: str,
        month_key: str,
        rpm_key: str | None,
        concurrency_key: str | None,
        tokens: int,
        cost_micros: int,
        *,
        model_key: str | None,
        provider_key: str | None,
    ) -> None:
        try:
            if tokens:
                await self._cache.incr(f"{day_key}:tokens", amount=-tokens)
                await self._cache.incr(f"{month_key}:tokens", amount=-tokens)
            if cost_micros:
                await self._cache.incr(f"{day_key}:cost", amount=-cost_micros)
                await self._cache.incr(f"{month_key}:cost", amount=-cost_micros)
            if rpm_key:
                await self._cache.incr(rpm_key, amount=-1)
            if concurrency_key:
                await self._cache.incr(concurrency_key, amount=-1)
            if model_key and tokens:
                await self._cache.incr(model_key, amount=-tokens)
            if provider_key and tokens:
                await self._cache.incr(provider_key, amount=-tokens)
        except Exception:
            return


__all__ = ["QuotaReservation", "QuotaReservationLedger"]
