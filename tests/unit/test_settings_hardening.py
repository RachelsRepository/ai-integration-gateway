"""Production settings hardening tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_gateway.config.settings import Settings


def test_production_rejects_literal_pepper() -> None:
    with pytest.raises(ValidationError, match="literal://"):
        Settings(
            environment="production",
            persistence_backend="postgres",
            auth={
                "jwt_enabled": False,
                "api_keys_enabled": True,
                "api_key_pepper_ref": "literal://not-allowed",
            },
            docs_enabled=False,
            security={"trusted_hosts": ("gateway.example.com",)},
            kafka={"enabled": True},
        )


def test_production_rejects_docs_and_wildcard_hosts() -> None:
    with pytest.raises(ValidationError, match="OpenAPI documentation"):
        Settings(
            environment="staging",
            persistence_backend="postgres",
            auth={
                "jwt_enabled": False,
                "api_keys_enabled": True,
                "api_key_pepper_ref": "env://AIGW_API_KEY_PEPPER",
            },
            docs_enabled=True,
            security={"trusted_hosts": ("gateway.example.com",)},
            kafka={"enabled": True},
        )


def test_production_rejects_memory_persistence() -> None:
    with pytest.raises(ValidationError, match="memory persistence"):
        Settings(
            environment="production",
            persistence_backend="memory",
            auth={
                "jwt_enabled": False,
                "api_keys_enabled": True,
                "api_key_pepper_ref": "env://AIGW_API_KEY_PEPPER",
            },
            docs_enabled=False,
            security={"trusted_hosts": ("gateway.example.com",)},
            kafka={"enabled": True},
        )


def test_production_rejects_disabled_kafka() -> None:
    with pytest.raises(ValidationError, match="Kafka must be enabled"):
        Settings(
            environment="production",
            persistence_backend="postgres",
            auth={
                "jwt_enabled": False,
                "api_keys_enabled": True,
                "api_key_pepper_ref": "env://AIGW_API_KEY_PEPPER",
            },
            docs_enabled=False,
            security={"trusted_hosts": ("gateway.example.com",)},
            kafka={"enabled": False},
        )


def test_production_accepts_hardened_config() -> None:
    settings = Settings(
        environment="production",
        persistence_backend="postgres",
        auth={
            "jwt_enabled": True,
            "api_keys_enabled": True,
            "api_key_pepper_ref": "env://AIGW_API_KEY_PEPPER",
            "jwks_url": "https://issuer.example.com/.well-known/jwks.json",
        },
        docs_enabled=False,
        security={"trusted_hosts": ("gateway.example.com",), "cors_allowed_origins": ()},
        kafka={"enabled": True},
    )
    assert settings.environment.value == "production"
    assert settings.persistence_backend.value == "postgres"
