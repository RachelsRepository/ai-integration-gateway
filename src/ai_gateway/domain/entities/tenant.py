"""Tenant, principal and quota entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ai_gateway.domain.errors import AuthorizationError, ValidationError
from ai_gateway.domain.value_objects.identifiers import ApiKeyId, TenantId, UserId, new_id
from ai_gateway.domain.value_objects.model import ModelTier
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName


class TenantStatus(StrEnum):
    """Lifecycle state of a tenant."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


class Role(StrEnum):
    """Coarse-grained roles assigned to principals."""

    ADMIN = "admin"
    OPERATOR = "operator"
    DEVELOPER = "developer"
    SERVICE = "service"
    READ_ONLY = "read_only"


class Permission(StrEnum):
    """Fine-grained permissions checked by use cases."""

    CHAT_INVOKE = "chat:invoke"
    EMBEDDINGS_INVOKE = "embeddings:invoke"
    AGENTS_RUN = "agents:run"
    PROMPTS_READ = "prompts:read"
    PROMPTS_WRITE = "prompts:write"
    MODELS_READ = "models:read"
    PROVIDERS_READ = "providers:read"
    USAGE_READ = "usage:read"
    TENANT_ADMIN = "tenant:admin"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.OPERATOR: frozenset(
        {
            Permission.CHAT_INVOKE,
            Permission.EMBEDDINGS_INVOKE,
            Permission.AGENTS_RUN,
            Permission.PROMPTS_READ,
            Permission.MODELS_READ,
            Permission.PROVIDERS_READ,
            Permission.USAGE_READ,
        }
    ),
    Role.DEVELOPER: frozenset(
        {
            Permission.CHAT_INVOKE,
            Permission.EMBEDDINGS_INVOKE,
            Permission.AGENTS_RUN,
            Permission.PROMPTS_READ,
            Permission.PROMPTS_WRITE,
            Permission.MODELS_READ,
            Permission.PROVIDERS_READ,
        }
    ),
    Role.SERVICE: frozenset(
        {
            Permission.CHAT_INVOKE,
            Permission.EMBEDDINGS_INVOKE,
            Permission.AGENTS_RUN,
            Permission.PROMPTS_READ,
            Permission.MODELS_READ,
        }
    ),
    Role.READ_ONLY: frozenset(
        {
            Permission.PROMPTS_READ,
            Permission.MODELS_READ,
            Permission.PROVIDERS_READ,
            Permission.USAGE_READ,
        }
    ),
}


class QuotaPeriod(StrEnum):
    """Window over which a quota is enforced."""

    DAILY = "daily"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class Quota:
    """A consumption ceiling applied to a tenant over a period.

    Attributes:
        period: Enforcement window.
        max_requests: Maximum requests allowed, or ``None`` for unlimited.
        max_tokens: Maximum billable tokens allowed, or ``None`` for unlimited.
        max_cost: Maximum spend allowed, or ``None`` for unlimited.
    """

    period: QuotaPeriod
    max_requests: int | None = None
    max_tokens: int | None = None
    max_cost: Money | None = None

    def __post_init__(self) -> None:
        """Validate the quota.

        Raises:
            ValidationError: If a limit is negative.
        """
        for name, value in (("max_requests", self.max_requests), ("max_tokens", self.max_tokens)):
            if value is not None and value < 0:
                raise ValidationError(f"{name} must not be negative")
        if self.max_cost is not None and self.max_cost.amount < 0:
            raise ValidationError("max_cost must not be negative")

    @property
    def is_unlimited(self) -> bool:
        """Return ``True`` when the quota imposes no ceiling."""
        return self.max_requests is None and self.max_tokens is None and self.max_cost is None


@dataclass(frozen=True, slots=True)
class RoutingPreferences:
    """Tenant-scoped constraints applied during model routing.

    Attributes:
        allowed_providers: Providers the tenant may use; empty means all configured.
        denied_providers: Providers explicitly blocked, evaluated after ``allowed``.
        allowed_models: Qualified model references the tenant may use; empty means all.
        preferred_provider: Provider tried first when it satisfies the request.
        max_tier: Highest cost tier the tenant is entitled to.
        data_residency: Optional residency constraint matched against provider regions.
        require_streaming_support: Restrict routing to models that can stream.
        max_cost_per_request: Reject routing candidates above this projected cost.
    """

    allowed_providers: frozenset[ProviderName] = frozenset()
    denied_providers: frozenset[ProviderName] = frozenset()
    allowed_models: frozenset[str] = frozenset()
    preferred_provider: ProviderName | None = None
    max_tier: ModelTier = ModelTier.PREMIUM
    data_residency: str | None = None
    require_streaming_support: bool = False
    max_cost_per_request: Money | None = None

    def permits_provider(self, provider: ProviderName) -> bool:
        """Report whether a provider is usable by the tenant.

        Args:
            provider: Candidate provider.

        Returns:
            ``True`` when the provider is permitted.
        """
        if provider in self.denied_providers:
            return False
        return not self.allowed_providers or provider in self.allowed_providers

    def permits_model(self, qualified_model: str) -> bool:
        """Report whether a qualified model reference is usable by the tenant.

        Args:
            qualified_model: Reference in ``provider/model`` form.

        Returns:
            ``True`` when the model is permitted.
        """
        return not self.allowed_models or qualified_model in self.allowed_models


@dataclass(slots=True)
class Tenant:
    """An isolated customer of the gateway.

    Attributes:
        name: Human readable tenant name.
        id: Stable tenant identifier.
        status: Lifecycle state.
        quotas: Quotas keyed by enforcement period.
        rate_limit_per_minute: Sustained request rate ceiling.
        rate_limit_burst: Additional burst allowance above the sustained rate.
        routing: Routing constraints.
        pii_redaction_enabled: Whether prompt content is redacted before egress.
        injection_detection_enabled: Whether prompt-injection screening is enforced.
        audit_retention_days: Retention applied to audit records.
        created_at: Creation timestamp in UTC.
        metadata: Free-form annotations.
    """

    name: str
    id: TenantId = field(default_factory=lambda: TenantId(new_id()))
    status: TenantStatus = TenantStatus.ACTIVE
    quotas: dict[QuotaPeriod, Quota] = field(default_factory=dict)
    rate_limit_per_minute: int = 600
    rate_limit_burst: int = 60
    routing: RoutingPreferences = field(default_factory=RoutingPreferences)
    pii_redaction_enabled: bool = True
    injection_detection_enabled: bool = True
    audit_retention_days: int = 365
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Return ``True`` when the tenant may issue requests."""
        return self.status is TenantStatus.ACTIVE

    def quota_for(self, period: QuotaPeriod) -> Quota:
        """Return the quota for a period, defaulting to unlimited.

        Args:
            period: Enforcement window.

        Returns:
            The configured quota, or an unlimited quota when none is set.
        """
        return self.quotas.get(period, Quota(period=period))

    def assert_active(self) -> None:
        """Ensure the tenant is permitted to issue requests.

        Raises:
            AuthorizationError: If the tenant is suspended or disabled.
        """
        if not self.is_active:
            raise AuthorizationError(
                "Tenant is not active",
                details={"tenant_id": self.id, "status": self.status.value},
            )


@dataclass(frozen=True, slots=True)
class ApiKey:
    """A hashed, tenant-scoped API credential.

    The plaintext secret is never stored; only a keyed hash is persisted. The ``prefix``
    is a non-secret fragment used for support and log correlation.

    Attributes:
        tenant_id: Owning tenant.
        prefix: Non-secret leading fragment of the key.
        hashed_secret: Keyed hash of the plaintext secret.
        id: Stable credential identifier.
        name: Human readable label.
        roles: Roles granted to bearers of the key.
        scopes: Optional narrowing of the roles' permissions.
        created_at: Creation timestamp in UTC.
        expires_at: Optional expiry timestamp.
        last_used_at: Timestamp of the most recent successful authentication.
        revoked_at: Revocation timestamp, if revoked.
    """

    tenant_id: TenantId
    prefix: str
    hashed_secret: str
    id: ApiKeyId = field(default_factory=lambda: ApiKeyId(new_id()))
    name: str = "default"
    roles: frozenset[Role] = frozenset({Role.SERVICE})
    scopes: frozenset[str] = frozenset()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    def is_valid_at(self, now: datetime) -> bool:
        """Report whether the credential is usable at a point in time.

        Args:
            now: Evaluation time.

        Returns:
            ``True`` when the key is neither revoked nor expired.
        """
        if self.revoked_at is not None:
            return False
        return not (self.expires_at is not None and self.expires_at <= now)


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated identity behind a request.

    Attributes:
        tenant_id: Tenant the principal acts for.
        subject: Stable subject identifier from the credential.
        roles: Roles granted to the principal.
        auth_method: How the principal authenticated.
        user_id: Optional end-user identifier.
        scopes: OAuth2 scopes presented with the credential.
        api_key_id: Identifier of the API key used, when applicable.
        claims: Additional non-authoritative claims for auditing.
    """

    tenant_id: TenantId
    subject: str
    roles: frozenset[Role]
    auth_method: str
    user_id: UserId | None = None
    scopes: frozenset[str] = frozenset()
    api_key_id: ApiKeyId | None = None
    claims: dict[str, str] = field(default_factory=dict)

    @property
    def permissions(self) -> frozenset[Permission]:
        """Return the union of permissions granted by the principal's roles."""
        granted: set[Permission] = set()
        for role in self.roles:
            granted |= ROLE_PERMISSIONS.get(role, frozenset())
        return frozenset(granted)

    def has_permission(self, permission: Permission) -> bool:
        """Report whether the principal holds a permission.

        Args:
            permission: Permission to test.

        Returns:
            ``True`` when granted.
        """
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        """Enforce that the principal holds a permission.

        Args:
            permission: Permission to enforce.

        Raises:
            AuthorizationError: If the permission is not granted.
        """
        if not self.has_permission(permission):
            raise AuthorizationError(
                "Principal lacks the required permission",
                details={
                    "required": permission.value,
                    "roles": sorted(role.value for role in self.roles),
                },
            )


__all__ = [
    "ROLE_PERMISSIONS",
    "ApiKey",
    "Permission",
    "Principal",
    "Quota",
    "QuotaPeriod",
    "Role",
    "RoutingPreferences",
    "Tenant",
    "TenantStatus",
]
