"""Domain event tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from ai_gateway.domain.events import DomainEvent, EventType, jsonable
from ai_gateway.domain.value_objects.identifiers import RequestId, TenantId


class _Color(Enum):
    RED = "red"


@dataclass
class _Payload:
    name: str
    count: int


def test_event_type_topics() -> None:
    assert EventType.USAGE_RECORDED.topic == "gateway.usage"
    assert EventType.QUOTA_EXCEEDED.topic == "gateway.governance"


def test_domain_event_serialisation() -> None:
    tenant_id = TenantId("11111111-1111-4111-8111-111111111111")
    event = DomainEvent(
        type=EventType.PROMPT_SUBMITTED,
        tenant_id=tenant_id,
        payload={"model": "echo/echo-1"},
        request_id=RequestId("req-1"),
        trace_id="trace-1",
    )
    assert event.topic == "gateway.requests"
    assert event.partition_key == str(tenant_id)
    data = event.to_dict()
    assert data["type"] == EventType.PROMPT_SUBMITTED.value
    assert data["payload"]["model"] == "echo/echo-1"


def test_jsonable_converts_nested_values() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = jsonable(
        {
            "when": now,
            "cost": Decimal("1.23"),
            "color": _Color.RED,
            "payload": _Payload(name="x", count=2),
            "items": frozenset({1, 2}),
        }
    )
    assert result["when"] == now.isoformat()
    assert result["cost"] == "1.23"
    assert result["color"] == "red"
    assert result["payload"]["name"] == "x"
    assert result["items"] == [1, 2]
