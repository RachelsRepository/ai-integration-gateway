"""Usage and prompt use case tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from ai_gateway.application.dto import PromptPublishCommand, RequestContext
from ai_gateway.application.use_cases.base import GatewayServices
from ai_gateway.application.use_cases.prompts import (
    GetPromptUseCase,
    ListPromptsUseCase,
    PublishPromptUseCase,
)
from ai_gateway.application.use_cases.usage import GetUsageReportUseCase
from ai_gateway.domain.entities.usage import UsageAggregate
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.tokens import TokenUsage


@pytest.mark.asyncio
async def test_get_usage_report(
    seeded_services: GatewayServices, request_context: RequestContext, tenant: object
) -> None:
    tenant_id = tenant.id  # type: ignore[attr-defined]
    async with seeded_services.uow_factory() as uow:
        await uow.usage.upsert_aggregate(
            UsageAggregate(
                tenant_id=tenant_id,
                period_key="2026-01-15",
                model="echo/echo-1",
                requests=2,
                usage=TokenUsage(prompt_tokens=20, completion_tokens=10),
                cost=Money.of("0.01"),
            )
        )
        await uow.usage.upsert_aggregate(
            UsageAggregate(
                tenant_id=tenant_id,
                period_key="2026-01-15",
                model="*",
                requests=99,
            )
        )
        await uow.commit()

    report = await GetUsageReportUseCase(seeded_services).execute(
        request_context,
        since=datetime(2026, 1, 1).date(),
        until=datetime(2026, 1, 31).date(),
    )
    assert report.requests == 2
    assert report.usage.total_tokens == 30
    assert "echo/echo-1" in report.by_model


@pytest.mark.asyncio
async def test_prompt_publish_get_list(
    seeded_services: GatewayServices, request_context: RequestContext
) -> None:
    publish = PublishPromptUseCase(seeded_services)
    view = await publish.execute(
        PromptPublishCommand(
            name="welcome",
            template="Hello {{ name }}",
            description="Greeting",
            system_prompt="Be kind",
            required_variables=frozenset({"name"}),
            activate=True,
            notes="v1",
            labels={"team": "platform"},
        ),
        request_context,
    )
    assert view.name == "welcome"
    assert view.active_version == 1

    fetched = await GetPromptUseCase(seeded_services).execute("welcome", request_context)
    assert fetched.id == view.id

    listed = await ListPromptsUseCase(seeded_services).execute(request_context)
    assert any(p.name == "welcome" for p in listed)

    updated = await publish.execute(
        PromptPublishCommand(
            name="welcome",
            template="Hi {{ name }}",
            description="Updated greeting",
            activate=True,
        ),
        request_context,
    )
    assert updated.active_version == 2
