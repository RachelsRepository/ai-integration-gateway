"""Provider composition root helper."""

from __future__ import annotations

from ai_gateway.application.ports.llm_provider import LLMProvider
from ai_gateway.application.ports.secrets import SecretResolver
from ai_gateway.config.settings import ProviderSettings
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.infrastructure.providers.anthropic import AnthropicProvider
from ai_gateway.infrastructure.providers.azure_openai import AzureOpenAIProvider
from ai_gateway.infrastructure.providers.bedrock import BedrockProvider
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog
from ai_gateway.infrastructure.providers.echo import EchoProvider
from ai_gateway.infrastructure.providers.google import GoogleGeminiProvider
from ai_gateway.infrastructure.providers.openai import OpenAIProvider
from ai_gateway.observability.logging import get_logger

logger = get_logger(__name__)


async def build_providers(
    settings: ProviderSettings,
    secrets: SecretResolver,
    *,
    catalog: StaticModelCatalog | None = None,
) -> dict[ProviderName, LLMProvider]:
    """Construct adapters for every enabled provider with resolvable credentials.

    Args:
        settings: Provider settings.
        secrets: Secret resolver.
        catalog: Shared model catalogue.

    Returns:
        Mapping of provider name to adapter. The echo provider is always available when
        enabled so local development and tests never depend on external credentials.
    """
    catalog = catalog or StaticModelCatalog()
    enabled = {ProviderName.parse(name) for name in settings.enabled}
    providers: dict[ProviderName, LLMProvider] = {}

    if ProviderName.ECHO in enabled:
        providers[ProviderName.ECHO] = EchoProvider(catalog=catalog)

    if ProviderName.OPENAI in enabled:
        api_key = await secrets.resolve_optional(settings.openai_api_key_ref)
        if api_key:
            providers[ProviderName.OPENAI] = OpenAIProvider(
                api_key=api_key,
                base_url=settings.openai_base_url,
                organization=settings.openai_organization,
                catalog=catalog,
            )
        else:
            logger.warning("provider_skipped", provider="openai", reason="missing_api_key")

    if ProviderName.ANTHROPIC in enabled:
        api_key = await secrets.resolve_optional(settings.anthropic_api_key_ref)
        if api_key:
            providers[ProviderName.ANTHROPIC] = AnthropicProvider(
                api_key=api_key,
                base_url=settings.anthropic_base_url,
                version=settings.anthropic_version,
                catalog=catalog,
            )
        else:
            logger.warning("provider_skipped", provider="anthropic", reason="missing_api_key")

    if ProviderName.GOOGLE in enabled:
        api_key = await secrets.resolve_optional(settings.google_api_key_ref)
        if api_key:
            providers[ProviderName.GOOGLE] = GoogleGeminiProvider(
                api_key=api_key,
                base_url=settings.google_base_url,
                catalog=catalog,
            )
        else:
            logger.warning("provider_skipped", provider="google", reason="missing_api_key")

    if ProviderName.AZURE_OPENAI in enabled:
        api_key = await secrets.resolve_optional(settings.azure_openai_api_key_ref)
        if api_key and settings.azure_openai_endpoint:
            providers[ProviderName.AZURE_OPENAI] = AzureOpenAIProvider(
                api_key=api_key,
                endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                deployments=settings.azure_openai_deployments,
                catalog=catalog,
            )
        else:
            logger.warning("provider_skipped", provider="azure_openai", reason="missing_config")

    if ProviderName.BEDROCK in enabled:
        access_key = await secrets.resolve_optional(settings.bedrock_access_key_id_ref)
        secret_key = await secrets.resolve_optional(settings.bedrock_secret_access_key_ref)
        session = await secrets.resolve_optional(settings.bedrock_session_token_ref)
        if access_key and secret_key:
            providers[ProviderName.BEDROCK] = BedrockProvider(
                region=settings.bedrock_region,
                access_key_id=access_key,
                secret_access_key=secret_key,
                session_token=session,
                endpoint=settings.bedrock_endpoint,
                catalog=catalog,
            )
        else:
            logger.warning("provider_skipped", provider="bedrock", reason="missing_credentials")

    return providers


__all__ = ["build_providers"]
