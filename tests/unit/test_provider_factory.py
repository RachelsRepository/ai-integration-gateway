"""Provider factory tests."""

from __future__ import annotations

import pytest

from ai_gateway.config.settings import ProviderSettings
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.infrastructure.providers.factory import build_providers
from ai_gateway.infrastructure.secrets.resolver import CompositeSecretResolver


@pytest.mark.asyncio
async def test_build_providers_with_all_credentials() -> None:
    secrets = CompositeSecretResolver(allow_literals=True)
    settings = ProviderSettings(
        enabled=("echo", "openai", "anthropic", "google", "azure_openai", "bedrock"),
        openai_api_key_ref="literal://openai-key",
        anthropic_api_key_ref="literal://anthropic-key",
        google_api_key_ref="literal://google-key",
        azure_openai_api_key_ref="literal://azure-key",
        azure_openai_endpoint="https://example.openai.azure.com",
        bedrock_access_key_id_ref="literal://access",
        bedrock_secret_access_key_ref="literal://secret",
        bedrock_session_token_ref="literal://session",
    )
    providers = await build_providers(settings, secrets)
    assert ProviderName.ECHO in providers
    assert ProviderName.OPENAI in providers
    assert ProviderName.ANTHROPIC in providers
    assert ProviderName.GOOGLE in providers
    assert ProviderName.AZURE_OPENAI in providers
    assert ProviderName.BEDROCK in providers


@pytest.mark.asyncio
async def test_build_providers_skips_missing_keys() -> None:
    secrets = CompositeSecretResolver(allow_literals=True)
    settings = ProviderSettings(
        enabled=("openai", "anthropic", "google", "azure_openai", "bedrock"),
    )
    providers = await build_providers(settings, secrets)
    assert providers == {}
