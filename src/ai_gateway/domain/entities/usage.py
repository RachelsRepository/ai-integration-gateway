"""Usage metering entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

from ai_gateway.domain.errors import ValidationError
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId, UserId, new_id
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.domain.value_objects.tokens import TokenUsage


class OperationType(StrEnum):
    """The billable operation a usage record describes."""

    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    RESPONSES = "responses"
    AGENT = "agent"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """An immutable, per-request metering fact.

    Usage records are append-only. Aggregation happens downstream so that the request
    path never contends on a shared counter row.

    Attributes:
        tenant_id: Billed tenant.
        request_id: Correlating request identifier.
        operation: Billable operation type.
        model: Model that served the request.
        usage: Token counters.
        cost: Computed cost of the call.
        latency_ms: End-to-end latency measured by the gateway.
        occurred_at: Time the request completed, in UTC.
        id: Stable record identifier.
        user_id: Optional end-user attribution.
        provider: Provider that served the request.
        succeeded: Whether the upstream call succeeded.
        cached: Whether the response was served from the gateway response cache.
        attempt: Attempt number, starting at one, for retried requests.
        metadata: Free-form annotations such as route or agent identifiers.
    """

    tenant_id: TenantId
    request_id: RequestId
    operation: OperationType
    model: ModelRef
    usage: TokenUsage
    cost: Money
    latency_ms: int
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)
    user_id: UserId | None = None
    provider: ProviderName | None = None
    succeeded: bool = True
    cached: bool = False
    attempt: int = 1
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise derived fields and validate invariants.

        Raises:
            ValidationError: If latency or attempt counters are invalid.
        """
        if self.latency_ms < 0:
            raise ValidationError("latency_ms must not be negative")
        if self.attempt < 1:
            raise ValidationError("attempt must start at 1")
        if self.provider is None:
            object.__setattr__(self, "provider", self.model.provider)

    @property
    def usage_date(self) -> date:
        """Return the UTC calendar date used for daily aggregation."""
        return self.occurred_at.astimezone(UTC).date()

    @property
    def billing_month(self) -> str:
        """Return the ``YYYY-MM`` bucket used for monthly aggregation."""
        moment = self.occurred_at.astimezone(UTC)
        return f"{moment.year:04d}-{moment.month:02d}"


@dataclass(slots=True)
class UsageAggregate:
    """A rolled-up view of usage for a tenant, period and model.

    Attributes:
        tenant_id: Billed tenant.
        period_key: Either ``YYYY-MM-DD`` or ``YYYY-MM``.
        model: Qualified model reference, or ``"*"`` for the tenant-wide roll-up.
        requests: Number of requests counted.
        failed_requests: Number of failed requests counted.
        cached_requests: Number of responses served from cache.
        usage: Accumulated token counters.
        cost: Accumulated cost.
        updated_at: Timestamp of the last accumulation.
    """

    tenant_id: TenantId
    period_key: str
    model: str = "*"
    requests: int = 0
    failed_requests: int = 0
    cached_requests: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage.empty)
    cost: Money = field(default_factory=Money.zero)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def accumulate(self, record: UsageRecord) -> None:
        """Fold a usage record into the aggregate.

        Args:
            record: The record to accumulate.

        Raises:
            ValidationError: If the record belongs to a different tenant.
        """
        if record.tenant_id != self.tenant_id:
            raise ValidationError("Cannot aggregate usage across tenants")
        self.requests += 1
        if not record.succeeded:
            self.failed_requests += 1
        if record.cached:
            self.cached_requests += 1
        self.usage = self.usage + record.usage
        self.cost = self.cost + record.cost
        self.updated_at = record.occurred_at

    @property
    def total_tokens(self) -> int:
        """Return the accumulated billable token count."""
        return self.usage.total_tokens


__all__ = ["OperationType", "UsageAggregate", "UsageRecord"]
