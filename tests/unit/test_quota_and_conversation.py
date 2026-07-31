"""Quota policy and conversation aggregate tests."""

from __future__ import annotations

import pytest

from ai_gateway.domain.entities.conversation import Conversation
from ai_gateway.domain.entities.message import Message
from ai_gateway.domain.entities.tenant import Quota, QuotaPeriod, Tenant
from ai_gateway.domain.errors import BudgetExceededError, QuotaExceededError
from ai_gateway.domain.policies.quota import QuotaEvaluator, UsageSnapshot
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.domain.value_objects.money import Money


def test_quota_evaluator_requests_and_budget() -> None:
    tenant = Tenant(
        name="q",
        id=TenantId("t"),
        quotas={
            QuotaPeriod.DAILY: Quota(
                period=QuotaPeriod.DAILY, max_requests=2, max_cost=Money.of("1")
            ),
        },
    )
    evaluator = QuotaEvaluator()
    decision = evaluator.evaluate(
        tenant,
        {QuotaPeriod.DAILY: UsageSnapshot(period=QuotaPeriod.DAILY, requests=1)},
    )
    assert decision.allowed

    denied = evaluator.evaluate(
        tenant,
        {QuotaPeriod.DAILY: UsageSnapshot(period=QuotaPeriod.DAILY, requests=2)},
    )
    assert not denied.allowed
    with pytest.raises(QuotaExceededError):
        denied.raise_if_denied()

    budget = evaluator.evaluate(
        tenant,
        {
            QuotaPeriod.DAILY: UsageSnapshot(
                period=QuotaPeriod.DAILY, requests=0, cost=Money.of("0.9")
            )
        },
        projected_cost=Money.of("0.2"),
    )
    with pytest.raises(BudgetExceededError):
        budget.raise_if_denied()


def test_conversation_append_trim_and_ownership() -> None:
    conversation = Conversation(tenant_id=TenantId("t1"))
    conversation.append(Message.system("sys"))
    conversation.append(Message.user("one"))
    conversation.append(Message.user("two three four five six seven eight nine ten"))
    removed = conversation.trim_to_token_budget(20)
    assert conversation.system_prompt == "sys"
    assert isinstance(removed, list)
    conversation.assert_owned_by(TenantId("t1"))
    from ai_gateway.domain.errors import ValidationError

    with pytest.raises(ValidationError):
        conversation.assert_owned_by(TenantId("other"))
