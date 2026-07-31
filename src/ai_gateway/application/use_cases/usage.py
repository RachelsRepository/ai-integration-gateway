"""Usage reporting use case."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ai_gateway.application.dto import RequestContext, UsageReport
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.domain.entities.tenant import Permission
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.tokens import TokenUsage


class GetUsageReportUseCase:
    """Serves ``GET /v1/usage``."""

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators.
        """
        self._s = services

    async def execute(
        self,
        context: RequestContext,
        *,
        since: date | None = None,
        until: date | None = None,
    ) -> UsageReport:
        """Aggregate a tenant's consumption over a date range.

        Args:
            context: Request context.
            since: Inclusive start date; defaults to 30 days before ``until``.
            until: Inclusive end date; defaults to today.

        Returns:
            The usage report.
        """
        context.principal.require(Permission.USAGE_READ)
        end = until or self._s.clock.now().date()
        start = since or (end - timedelta(days=30))

        async with self._s.uow_factory() as uow:
            aggregates = await uow.usage.aggregates_for(context.tenant_id, since=start, until=end)

        requests = 0
        usage = TokenUsage.empty()
        cost = Money.zero()
        by_model: dict[str, Money] = {}
        for aggregate in aggregates:
            if aggregate.model == "*":
                continue
            requests += aggregate.requests
            usage = usage + aggregate.usage
            cost = cost + aggregate.cost
            by_model[aggregate.model] = by_model.get(aggregate.model, Money.zero()) + aggregate.cost

        return UsageReport(
            tenant_id=context.tenant_id,
            period_start=datetime.combine(start, time.min),
            period_end=datetime.combine(end, time.max),
            requests=requests,
            usage=usage,
            cost=cost,
            by_model=by_model,
        )


__all__ = ["GetUsageReportUseCase"]
