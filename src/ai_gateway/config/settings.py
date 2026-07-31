"""Application settings.

Configuration is environment-driven and strictly typed. Secrets are never stored as
plaintext defaults: every credential field holds a *reference* that the configured
:class:`~ai_gateway.application.ports.secrets.SecretResolver` dereferences at start-up.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PREFIX = "AIGW_"


class Environment(StrEnum):
    """Deployment environment."""

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production_like(self) -> bool:
        """Return ``True`` for environments that must enforce hardened defaults."""
        return self in {Environment.STAGING, Environment.PRODUCTION}


class _Base(BaseSettings):
    """Base settings model with shared configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )


class DatabaseSettings(_Base):
    """PostgreSQL connection settings."""

    model_config = SettingsConfigDict(env_prefix=f"{_ENV_PREFIX}DB_", extra="ignore", frozen=True)

    dsn: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    max_overflow: Annotated[int, Field(ge=0, le=100)] = 10
    pool_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0
    pool_recycle_seconds: Annotated[int, Field(gt=0)] = 1800
    statement_timeout_ms: Annotated[int, Field(gt=0)] = 15_000
    echo: bool = False

    @field_validator("dsn")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """Ensure the DSN uses an async driver.

        Args:
            value: Configured DSN.

        Returns:
            The validated DSN.

        Raises:
            ValueError: If the DSN would select a blocking driver.
        """
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("Database DSN must use the postgresql+asyncpg driver")
        return value


class RedisSettings(_Base):
    """Redis connection and cache settings."""

    model_config = SettingsConfigDict(
        env_prefix=f"{_ENV_PREFIX}REDIS_", extra="ignore", frozen=True
    )

    url: str = "redis://localhost:6379/0"
    max_connections: Annotated[int, Field(ge=1)] = 50
    socket_timeout_seconds: Annotated[float, Field(gt=0)] = 2.0
    response_cache_ttl_seconds: Annotated[int, Field(ge=0)] = 300
    embedding_cache_ttl_seconds: Annotated[int, Field(ge=0)] = 86_400
    prompt_cache_ttl_seconds: Annotated[int, Field(ge=0)] = 600
    lock_ttl_seconds: Annotated[int, Field(gt=0)] = 30


class KafkaSettings(_Base):
    """Kafka producer settings."""

    model_config = SettingsConfigDict(
        env_prefix=f"{_ENV_PREFIX}KAFKA_", extra="ignore", frozen=True
    )

    enabled: bool = True
    bootstrap_servers: str = "localhost:9092"
    client_id: str = "ai-gateway"
    topic_prefix: str = ""
    security_protocol: Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"] = "PLAINTEXT"
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password_ref: str | None = None
    acks: Literal["0", "1", "all"] = "all"
    linger_ms: Annotated[int, Field(ge=0)] = 20
    request_timeout_ms: Annotated[int, Field(gt=0)] = 10_000
    outbox_poll_interval_seconds: Annotated[float, Field(gt=0)] = 1.0
    outbox_batch_size: Annotated[int, Field(gt=0)] = 200


class AuthSettings(_Base):
    """Authentication and authorisation settings."""

    model_config = SettingsConfigDict(env_prefix=f"{_ENV_PREFIX}AUTH_", extra="ignore", frozen=True)

    jwt_enabled: bool = True
    jwt_issuer: str = "https://issuer.example.com/"
    jwt_audience: str = "ai-gateway"
    jwt_algorithms: tuple[str, ...] = ("RS256",)
    jwks_url: str | None = None
    jwks_cache_seconds: Annotated[int, Field(gt=0)] = 300
    jwt_leeway_seconds: Annotated[int, Field(ge=0)] = 30
    jwt_shared_secret_ref: str | None = None
    tenant_claim: str = "tenant_id"
    roles_claim: str = "roles"
    scope_claim: str = "scope"

    api_keys_enabled: bool = True
    api_key_header: str = "X-API-Key"
    api_key_prefix_length: Annotated[int, Field(ge=4, le=16)] = 8
    api_key_pepper_ref: str = "literal://local-dev-pepper-change-me"

    oauth2_token_url: str = "https://issuer.example.com/oauth2/token"
    oauth2_authorization_url: str = "https://issuer.example.com/oauth2/authorize"
    allow_anonymous_health: bool = True


class SecuritySettings(_Base):
    """Request-security settings."""

    model_config = SettingsConfigDict(
        env_prefix=f"{_ENV_PREFIX}SECURITY_", extra="ignore", frozen=True
    )

    request_signing_enabled: bool = False
    signing_secret_ref: str = "env://AIGW_SIGNING_SECRET"
    signature_header: str = "X-Signature"
    signature_timestamp_header: str = "X-Signature-Timestamp"
    signature_max_skew_seconds: Annotated[int, Field(gt=0)] = 300
    webhook_secret_ref: str = "env://AIGW_WEBHOOK_SECRET"

    credential_encryption_key_ref: str = "env://AIGW_ENCRYPTION_KEY"
    pii_redaction_enabled: bool = True
    prompt_injection_detection_enabled: bool = True
    injection_block_threshold: Literal["low", "medium", "high"] = "high"
    output_filter_block_on_secret: bool = True
    max_request_bytes: Annotated[int, Field(gt=0)] = 1_048_576
    max_messages_per_request: Annotated[int, Field(gt=0)] = 200
    cors_allowed_origins: tuple[str, ...] = ()
    trusted_hosts: tuple[str, ...] = ("*",)


class ResilienceSettings(_Base):
    """Retry, timeout and circuit breaker settings."""

    model_config = SettingsConfigDict(
        env_prefix=f"{_ENV_PREFIX}RESILIENCE_", extra="ignore", frozen=True
    )

    request_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    provider_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    stream_idle_timeout_seconds: Annotated[float, Field(gt=0)] = 45.0
    retry_max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    retry_base_delay_seconds: Annotated[float, Field(ge=0)] = 0.2
    retry_max_delay_seconds: Annotated[float, Field(ge=0)] = 8.0
    retry_multiplier: Annotated[float, Field(ge=1)] = 2.0
    circuit_failure_threshold: Annotated[int, Field(ge=1)] = 5
    circuit_success_threshold: Annotated[int, Field(ge=1)] = 2
    circuit_reset_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    circuit_window_size: Annotated[int, Field(ge=10)] = 50
    dlq_max_attempts: Annotated[int, Field(ge=1)] = 5
    graceful_shutdown_seconds: Annotated[float, Field(ge=0)] = 15.0


class ProviderSettings(_Base):
    """Upstream provider credentials and endpoints.

    Every credential field holds a secret *reference*, never a literal. Supported schemes
    are ``env://NAME``, ``file:///path``, ``secretsmanager://name#key`` and ``vault://path#key``.
    """

    model_config = SettingsConfigDict(
        env_prefix=f"{_ENV_PREFIX}PROVIDER_", extra="ignore", frozen=True
    )

    enabled: tuple[str, ...] = ("echo",)
    default_model: str = "echo/echo-1"

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key_ref: str | None = None
    openai_organization: str | None = None

    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_api_key_ref: str | None = None
    anthropic_version: str = "2023-06-01"

    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    google_api_key_ref: str | None = None

    azure_openai_endpoint: str | None = None
    azure_openai_api_key_ref: str | None = None
    azure_openai_api_version: str = "2024-06-01"
    azure_openai_deployments: dict[str, str] = Field(default_factory=dict)

    bedrock_region: str = "us-east-1"
    bedrock_endpoint: str | None = None
    bedrock_access_key_id_ref: str | None = None
    bedrock_secret_access_key_ref: str | None = None
    bedrock_session_token_ref: str | None = None

    connect_timeout_seconds: Annotated[float, Field(gt=0)] = 5.0
    read_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    max_connections: Annotated[int, Field(ge=1)] = 100
    max_keepalive_connections: Annotated[int, Field(ge=1)] = 20
    health_check_interval_seconds: Annotated[float, Field(gt=0)] = 30.0


class ObservabilitySettings(_Base):
    """Logging, metrics and tracing settings."""

    model_config = SettingsConfigDict(env_prefix=f"{_ENV_PREFIX}OTEL_", extra="ignore", frozen=True)

    service_name: str = "ai-integration-gateway"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    log_sample_rate: Annotated[float, Field(ge=0, le=1)] = 1.0
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    tracing_enabled: bool = False
    otlp_endpoint: str | None = None
    otlp_headers: dict[str, str] = Field(default_factory=dict)
    trace_sample_ratio: Annotated[float, Field(ge=0, le=1)] = 0.1
    correlation_header: str = "X-Request-ID"


class WorkerSettings(_Base):
    """Background worker scheduling settings."""

    model_config = SettingsConfigDict(
        env_prefix=f"{_ENV_PREFIX}WORKER_", extra="ignore", frozen=True
    )

    usage_aggregation_interval_seconds: Annotated[float, Field(gt=0)] = 60.0
    cost_calculation_interval_seconds: Annotated[float, Field(gt=0)] = 300.0
    conversation_cleanup_interval_seconds: Annotated[float, Field(gt=0)] = 3600.0
    conversation_retention_days: Annotated[int, Field(ge=1)] = 30
    audit_retention_days: Annotated[int, Field(ge=1)] = 365
    retry_queue_interval_seconds: Annotated[float, Field(gt=0)] = 30.0
    dlq_interval_seconds: Annotated[float, Field(gt=0)] = 120.0
    telemetry_export_interval_seconds: Annotated[float, Field(gt=0)] = 60.0
    batch_size: Annotated[int, Field(ge=1)] = 500


class Settings(_Base):
    """Root application settings."""

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = Environment.LOCAL
    service_name: str = "ai-integration-gateway"
    version: str = "1.0.0"
    region: str = "us-east-1"
    debug: bool = False
    host: str = "0.0.0.0"  # noqa: S104 - containers bind all interfaces by design
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    root_path: str = ""
    docs_enabled: bool = True
    default_tenant_rate_limit_per_minute: Annotated[int, Field(ge=1)] = 600
    admin_bootstrap_token: SecretStr | None = None

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    resilience: ResilienceSettings = Field(default_factory=ResilienceSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)

    @model_validator(mode="after")
    def _enforce_production_hardening(self) -> Settings:
        """Reject unsafe combinations in production-like environments.

        Returns:
            The validated settings.

        Raises:
            ValueError: If a hardening requirement is not met.
        """
        if not self.environment.is_production_like:
            return self
        if self.debug:
            raise ValueError("debug must be disabled outside development")
        if self.docs_enabled:
            raise ValueError("OpenAPI documentation must be disabled in production")
        if not self.auth.jwt_enabled and not self.auth.api_keys_enabled:
            raise ValueError("At least one authentication method must be enabled")
        if self.auth.jwt_enabled and not self.auth.jwks_url and not self.auth.jwt_shared_secret_ref:
            raise ValueError("JWT validation requires either a JWKS URL or a shared secret")
        if self.auth.api_key_pepper_ref.startswith("literal://"):
            raise ValueError("API key pepper must not use literal:// references in production")
        if "change-me" in self.auth.api_key_pepper_ref.lower():
            raise ValueError("API key pepper still uses a placeholder value")
        if "*" in self.security.cors_allowed_origins:
            raise ValueError("Wildcard CORS origins are not permitted in production")
        if "*" in self.security.trusted_hosts:
            raise ValueError("Wildcard trusted hosts are not permitted in production")
        return self

    @property
    def is_local(self) -> bool:
        """Return ``True`` when running against local, disposable infrastructure."""
        return self.environment is Environment.LOCAL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Returns:
        The cached settings instance.
    """
    return Settings()


__all__ = [
    "AuthSettings",
    "DatabaseSettings",
    "Environment",
    "KafkaSettings",
    "ObservabilitySettings",
    "ProviderSettings",
    "RedisSettings",
    "ResilienceSettings",
    "SecuritySettings",
    "Settings",
    "WorkerSettings",
    "get_settings",
]
