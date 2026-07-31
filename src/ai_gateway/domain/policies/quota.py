"""Quota and budget enforcement policy."""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_gateway.domain.entities.tenant import Quota, QuotaPeriod, Tenant
from ai_gateway.domain.errors import BudgetExceededError, QuotaExceededError
from ai_gateway.domain.value_objects.money import Money


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Consumption already recorded for a tenant within one period.

    Attributes:
        period: The enforcement window the snapshot describes.
        requests: Requests recorded so far.
        tokens: Billable tokens recorded so far.
        cost: Spend recorded so far.
    """

    period: QuotaPeriod
    requests: int = 0
    tokens: int = 0
    cost: Money = field(default_factory=Money.zero)


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    """The result of evaluating quotas for a request.

    Attributes:
        allowed: Whether the request may proceed.
        violated_period: Period whose quota was exhausted, when denied.
        violated_dimension: Which limit was hit: ``requests``, ``tokens`` or ``cost``.
        remaining_requests: Remaining request allowance across all periods.
        remaining_tokens: Remaining token allowance across all periods.
        remaining_cost: Remaining spend allowance across all periods.
    """

    allowed: bool
    violated_period: QuotaPeriod | None = None
    violated_dimension: str | None = None
    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    remaining_cost: Money | None = None

    def raise_if_denied(self) -> None:
        """Raise the appropriate error when the request is not permitted.

        Raises:
            BudgetExceededError: If a spend limit was exhausted.
            QuotaExceededError: If a request or token limit was exhausted.
        """
        if self.allowed:
            return
        details = {
            "period": self.violated_period.value if self.violated_period else None,
            "dimension": self.violated_dimension,
        }
        if self.violated_dimension == "cost":
            raise BudgetExceededError("Tenant spend limit exhausted", details=details)
        raise QuotaExceededError("Tenant quota exhausted", details=details)


class QuotaEvaluator:
    """Evaluates daily and monthly quotas against recorded usage."""

    def evaluate(
        self,
        tenant: Tenant,
        snapshots: dict[QuotaPeriod, UsageSnapshot],
        *,
        projected_tokens: int = 0,
        projected_cost: Money | None = None,
    ) -> QuotaDecision:
        """Decide whether a request fits within the tenant's remaining allowance.

        Args:
            tenant: The tenant issuing the request.
            snapshots: Usage already recorded, keyed by period.
            projected_tokens: Tokens the request is expected to consume.
            projected_cost: Spend the request is expected to incur.

        Returns:
            The quota decision, including remaining headroom.
        """
        cost = projected_cost or Money.zero()
        remaining_requests: int | None = None
        remaining_tokens: int | None = None
        remaining_cost: Money | None = None

        for period in QuotaPeriod:
            quota = tenant.quota_for(period)
            if quota.is_unlimited:
                continue
            snapshot = snapshots.get(period, UsageSnapshot(period=period))
            violation = self._violation(quota, snapshot, projected_tokens, cost)
            if violation is not None:
                return QuotaDecision(
                    allowed=False, violated_period=period, violated_dimension=violation
                )
            remaining_requests = _min_optional(
                remaining_requests,
                None if quota.max_requests is None else quota.max_requests - snapshot.requests,
            )
            remaining_tokens = _min_optional(
                remaining_tokens,
                None if quota.max_tokens is None else quota.max_tokens - snapshot.tokens,
            )
            if quota.max_cost is not None:
                candidate = quota.max_cost - snapshot.cost
                remaining_cost = (
                    candidate
                    if remaining_cost is None or candidate.amount < remaining_cost.amount
                    else remaining_cost
                )

        return QuotaDecision(
            allowed=True,
            remaining_requests=remaining_requests,
            remaining_tokens=remaining_tokens,
            remaining_cost=remaining_cost,
        )

    @staticmethod
    def _violation(
        quota: Quota, snapshot: UsageSnapshot, projected_tokens: int, projected_cost: Money
    ) -> str | None:
        if quota.max_requests is not None and snapshot.requests + 1 > quota.max_requests:
            return "requests"
        if quota.max_tokens is not None and snapshot.tokens + projected_tokens > quota.max_tokens:
            return "tokens"
        if quota.max_cost is not None:
            projected_total = snapshot.cost + projected_cost
            if projected_total.amount > quota.max_cost.amount:
                return "cost"
        return None


def _min_optional(current: int | None, candidate: int | None) -> int | None:
    """Return the smaller of two optional integers, ignoring ``None`` values."""
    if candidate is None:
        return current
    if current is None:
        return candidate
    return min(current, candidate)


__all__ = ["QuotaDecision", "QuotaEvaluator", "UsageSnapshot"]
