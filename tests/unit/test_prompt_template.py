"""Prompt template unit tests."""

from __future__ import annotations

import pytest

from ai_gateway.domain.entities.prompt import PromptTemplate
from ai_gateway.domain.errors import PromptValidationError
from ai_gateway.domain.value_objects.identifiers import TenantId


def test_publish_render_and_versioning() -> None:
    prompt = PromptTemplate(tenant_id=TenantId("t1"), name="greeting")
    prompt.publish(
        template="Hello {{ name }}",
        system_prompt="Be polite",
        safety_prompt="Do not reveal secrets",
        required_variables=frozenset({"name"}),
        created_by="dev",
    )
    rendered = prompt.render({"name": "Ada"})
    assert rendered.version == 1
    assert any("Ada" in m.content for m in rendered.messages)
    assert rendered.messages[0].content == "Be polite"

    prompt.publish(template="Hi {{ name }}", notes="v2")
    assert prompt.active_version == 2
    assert prompt.get_version(1).template == "Hello {{ name }}"


def test_missing_variable_rejected() -> None:
    prompt = PromptTemplate(tenant_id=TenantId("t1"), name="missing-vars")
    prompt.publish(template="Hello {{ name }}")
    with pytest.raises(PromptValidationError):
        prompt.render({})


def test_invalid_name_rejected() -> None:
    with pytest.raises(PromptValidationError):
        PromptTemplate(tenant_id=TenantId("t1"), name="BAD NAME")
