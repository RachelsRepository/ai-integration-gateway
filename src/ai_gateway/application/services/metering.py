"""Usage metering, quota enforcement and cost attribution."""

from __future__ import annotations

from ai_gateway.application.dto import RequestContext
from ai_gateway.application.ports.clock import Clock
from ai_gateway.application.ports.metrics import MetricsRecorder
from ai_gateway.application.ports.rate_limiter import RateLimiter
from ai_gateway.application.ports.repositories import UnitOfWork
from ai_gateway.domain.entities.tenant import QuotaPeriod, Tenant
from ai_gateway.domain.entities.usage import OperationType, UsageRecord
from ai_gateway.domain.events import DomainEvent, EventType
from ai_gateway.domain.policies.quota import QuotaDecision, QuotaEvaluator, UsageSnapshot
from ai_gateway.domain.services.cost import CostCalculator
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.tokens import TokenUsage


class UsageMeter:
    """Enforces rate limits and quotas, then records what a request consumed."""

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter,
        cost_calculator: CostCalculator,
        clock: Clock,
        metrics: MetricsRecorder,
        quota_evaluator: QuotaEvaluator | None = None,
    ) -> None:
        """Initialise the meter.

        Args:
            rate_limiter: Distributed rate limiter.
            cost_calculator: Price book backed cost calculator.
            clock: Injected clock.
            metrics: Metrics sink.
            quota_evaluator: Quota policy evaluator.
        """
        self._rate_limiter = rate_limiter
        self._costs = cost_calculator
        self._clock = clock
        self._metrics = metrics
        self._quotas = quota_evaluator or QuotaEvaluator()

    async def enforce_rate_limit(self, tenant: Tenant, *, cost: int = 1) -> None:
        """Consume rate-limit capacity for a tenant.

        Args:
            tenant: Tenant issuing the request.
            cost: Tokens consumed from the bucket.

        Raises:
            RateLimitExceededError: If the tenant is over its configured rate.
        """
        decision = await self._rate_limiter.acquire(
            f"tenant:{tenant.id}",
            limit_per_minute=tenant.rate_limit_per_minute,
            burst=tenant.rate_limit_burst,
            cost=cost,
        )
        self._metrics.set_gauge(
            "gateway_rate_limit_remaining",
            decision.remaining,
            labels={"tenant": str(tenant.id)},
        )
        if not decision.allowed:
            self._metrics.increment("gateway_rate_limited_total", labels={"tenant": str(tenant.id)})
        decision.raise_if_denied()

    async def enforce_quota(
        self,
        uow: UnitOfWork,
        tenant: Tenant,
        *,
        projected_tokens: int,
        projected_cost: Money,
    ) -> QuotaDecision:
        """Check the tenant's remaining daily and monthly allowance.

        Args:
            uow: Open unit of work supplying the usage snapshot.
            tenant: Tenant issuing the request.
            projected_tokens: Tokens the request is expected to consume.
            projected_cost: Spend the request is expected to incur.

        Returns:
            The quota decision when the request is permitted.

        Raises:
            QuotaExceededError: If a quota or budget is exhausted.
        """
        snapshots = await uow.usage.snapshot(tenant.id, at=self._clock.now())
        decision = self._quotas.evaluate(
            tenant,
            snapshots,
            projected_tokens=projected_tokens,
            projected_cost=projected_cost,
        )
        if not decision.allowed:
            self._metrics.increment(
                "gateway_quota_exceeded_total",
                labels={
                    "tenant": str(tenant.id),
                    "dimension": decision.violated_dimension or "unknown",
                },
            )
            await uow.outbox.enqueue(
                DomainEvent(
                    type=EventType.QUOTA_EXCEEDED,
                    tenant_id=tenant.id,
                    payload={
                        "period": decision.violated_period.value
                        if decision.violated_period
                        else None,
                        "dimension": decision.violated_dimension,
                    },
                )
            )
            decision.raise_if_denied()
        return decision

    def price(self, model: ModelRef, usage: TokenUsage) -> Money:
        """Compute the billable cost of a completed call.

        Args:
            model: Model that served the call.
            usage: Token counters reported by the provider.

        Returns:
            The billable cost.
        """
        return self._costs.calculate(model, usage)

    def project(self, model: ModelRef, *, prompt_tokens: int, max_output_tokens: int) -> Money:
        """Project the worst-case cost of a call before dispatch.

        Args:
            model: Candidate model.
            prompt_tokens: Estimated prompt size.
            max_output_tokens: Requested completion budget.

        Returns:
            The projected cost.
        """
        return self._costs.estimate(
            model, prompt_tokens=prompt_tokens, max_output_tokens=max_output_tokens
        )

    async def record(
        self,
        uow: UnitOfWork,
        context: RequestContext,
        *,
        operation: OperationType,
        model: ModelRef,
        usage: TokenUsage,
        cost: Money,
        latency_ms: int,
        succeeded: bool = True,
        cached: bool = False,
        attempt: int = 1,
        metadata: dict[str, str] | None = None,
    ) -> UsageRecord:
        """Persist a usage record and stage the corresponding event.

        Args:
            uow: Open unit of work.
            context: Request context supplying tenant and correlation identifiers.
            operation: Billable operation type.
            model: Model that served the request.
            usage: Token counters.
            cost: Billable cost.
            latency_ms: End-to-end latency.
            succeeded: Whether the upstream call succeeded.
            cached: Whether the response was served from cache.
            attempt: Attempt number for retried requests.
            metadata: Additional annotations.

        Returns:
            The persisted usage record.
        """
        record = UsageRecord(
            tenant_id=context.tenant_id,
            request_id=context.request_id,
            operation=operation,
            model=model,
            usage=usage,
            cost=cost,
            latency_ms=latency_ms,
            occurred_at=self._clock.now(),
            user_id=context.principal.user_id,
            provider=model.provider,
            succeeded=succeeded,
            cached=cached,
            attempt=attempt,
            metadata=metadata or {},
        )
        await uow.usage.record(record)
        await uow.outbox.enqueue(
            DomainEvent(
                type=EventType.USAGE_RECORDED,
                tenant_id=context.tenant_id,
                request_id=context.request_id,
                trace_id=context.trace_id,
                payload={
                    "usage_id": record.id,
                    "operation": operation.value,
                    "model": model.qualified,
                    "provider": model.provider.value,
                    "tokens": usage.as_dict(),
                    "cost_micros": cost.micros,
                    "currency": cost.currency,
                    "latency_ms": latency_ms,
                    "succeeded": succeeded,
                    "cached": cached,
                },
            )
        )
        self._emit_metrics(record)
        return record

    def _emit_metrics(self, record: UsageRecord) -> None:
        labels = {
            "tenant": str(record.tenant_id),
            "provider": record.model.provider.value,
            "model": record.model.name,
            "operation": record.operation.value,
        }
        self._metrics.increment("gateway_requests_total", labels=labels)
        self._metrics.increment(
            "gateway_tokens_total",
            value=float(record.usage.total_tokens),
            labels=labels,
        )
        self._metrics.increment(
            "gateway_cost_micros_total", value=float(record.cost.micros), labels=labels
        )
        self._metrics.observe("gateway_request_latency_ms", record.latency_ms, labels=labels)


def empty_snapshot(period: QuotaPeriod) -> UsageSnapshot:
    """Return a zeroed snapshot for a period.

    Args:
        period: Enforcement window.

    Returns:
        A snapshot with no recorded consumption.
    """
    return UsageSnapshot(period=period)


__all__ = ["UsageMeter", "empty_snapshot"]
