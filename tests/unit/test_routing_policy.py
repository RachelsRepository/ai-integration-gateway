"""Routing policy unit tests."""

from __future__ import annotations

import pytest

from ai_gateway.domain.entities.tenant import RoutingPreferences
from ai_gateway.domain.errors import NoProviderAvailableError
from ai_gateway.domain.policies.routing import (
    RoutingCandidate,
    RoutingPolicy,
    RoutingRequest,
    RoutingStrategy,
)
from ai_gateway.domain.value_objects.model import ModelCapability
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog


def _candidates() -> list[RoutingCandidate]:
    catalog = StaticModelCatalog()
    return [
        RoutingCandidate(spec=spec, status=ProviderStatus.HEALTHY, priority=index)
        for index, spec in enumerate(catalog.for_provider(ProviderName.ECHO))
        if ModelCapability.CHAT in spec.capabilities
    ] + [
        RoutingCandidate(spec=spec, status=ProviderStatus.HEALTHY, priority=10 + index)
        for index, spec in enumerate(catalog.for_provider(ProviderName.OPENAI))
        if ModelCapability.CHAT in spec.capabilities
    ]


def test_cost_optimized_prefers_cheaper_model() -> None:
    decision = RoutingPolicy().select(
        _candidates(),
        RoutingRequest(
            strategy=RoutingStrategy.COST_OPTIMIZED,
            required_capabilities=frozenset({ModelCapability.CHAT}),
        ),
    )
    assert "echo" in decision.selected.ref.qualified or "mini" in decision.selected.ref.qualified


def test_explicit_model_pin() -> None:
    decision = RoutingPolicy().select(
        _candidates(),
        RoutingRequest(
            requested_model="openai/gpt-4o-mini",
            required_capabilities=frozenset({ModelCapability.CHAT}),
        ),
    )
    assert decision.selected.ref.qualified == "openai/gpt-4o-mini"


def test_tenant_denylist_excludes_provider() -> None:
    with pytest.raises(NoProviderAvailableError):
        RoutingPolicy().select(
            _candidates(),
            RoutingRequest(
                preferences=RoutingPreferences(
                    denied_providers=frozenset(
                        {
                            ProviderName.ECHO,
                            ProviderName.OPENAI,
                            ProviderName.ANTHROPIC,
                            ProviderName.GOOGLE,
                            ProviderName.AZURE_OPENAI,
                            ProviderName.BEDROCK,
                        }
                    )
                ),
                required_capabilities=frozenset({ModelCapability.CHAT}),
            ),
        )


def test_unavailable_provider_is_rejected() -> None:
    unavailable = [
        RoutingCandidate(spec=c.spec, status=ProviderStatus.UNAVAILABLE, priority=c.priority)
        for c in _candidates()
    ]
    with pytest.raises(NoProviderAvailableError):
        RoutingPolicy().select(
            unavailable,
            RoutingRequest(required_capabilities=frozenset({ModelCapability.CHAT})),
        )
