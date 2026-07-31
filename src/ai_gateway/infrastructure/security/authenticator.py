"""Request authenticator resolving a Principal from API keys or JWTs."""

from __future__ import annotations

from ai_gateway.application.ports.clock import Clock
from ai_gateway.application.ports.repositories import UnitOfWork
from ai_gateway.domain.entities.tenant import Principal, Role, Tenant
from ai_gateway.domain.errors import AuthenticationError
from ai_gateway.domain.value_objects.identifiers import TenantId, UserId
from ai_gateway.infrastructure.security.api_keys import ApiKeyHasher
from ai_gateway.infrastructure.security.jwt_validator import JwtValidator


def _parse_roles(raw: tuple[str, ...]) -> frozenset[Role]:
    """Parse role claims accepting either enum names or values.

    Args:
        raw: Role claim values.

    Returns:
        The parsed roles, defaulting to ``SERVICE`` when none are recognised.
    """
    parsed: set[Role] = set()
    for role in raw:
        try:
            parsed.add(Role(role))
            continue
        except ValueError:
            pass
        try:
            parsed.add(Role[role.upper()])
        except KeyError:
            continue
    if not parsed:
        parsed.add(Role.SERVICE)
    return frozenset(parsed)


class Authenticator:
    """Resolves credentials into a principal and tenant."""

    def __init__(
        self,
        *,
        api_key_hasher: ApiKeyHasher | None = None,
        jwt_validator: JwtValidator | None = None,
        clock: Clock,
        api_keys_enabled: bool = True,
        jwt_enabled: bool = True,
    ) -> None:
        """Initialise the authenticator.

        Args:
            api_key_hasher: API key hasher; required when API keys are enabled.
            jwt_validator: JWT validator; required when JWT is enabled.
            clock: Injected clock.
            api_keys_enabled: Whether API key auth is accepted.
            jwt_enabled: Whether JWT auth is accepted.
        """
        self._hasher = api_key_hasher
        self._jwt = jwt_validator
        self._clock = clock
        self._api_keys_enabled = api_keys_enabled
        self._jwt_enabled = jwt_enabled

    async def authenticate(
        self,
        uow: UnitOfWork,
        *,
        api_key: str | None = None,
        bearer_token: str | None = None,
    ) -> tuple[Principal, Tenant]:
        """Authenticate a request.

        Args:
            uow: Open unit of work for credential and tenant lookup.
            api_key: Presented API key.
            bearer_token: Presented bearer token.

        Returns:
            The principal and its tenant.

        Raises:
            AuthenticationError: If no credential is valid.
        """
        if api_key and self._api_keys_enabled:
            return await self._from_api_key(uow, api_key)
        if bearer_token and self._jwt_enabled:
            return await self._from_jwt(uow, bearer_token)
        raise AuthenticationError("Missing or unsupported credentials")

    async def _from_api_key(self, uow: UnitOfWork, api_key: str) -> tuple[Principal, Tenant]:
        if self._hasher is None:
            raise AuthenticationError("API key authentication is not configured")
        prefix = self._hasher.prefix_of(api_key)
        candidates = await uow.api_keys.find_by_prefix(prefix)
        now = self._clock.now()
        for candidate in candidates:
            if not candidate.is_valid_at(now):
                continue
            if not self._hasher.verify(api_key, candidate.hashed_secret):
                continue
            tenant = await self._load_tenant(uow, candidate.tenant_id)
            await uow.api_keys.touch(candidate.id, at=now)
            principal = Principal(
                tenant_id=candidate.tenant_id,
                subject=f"apikey:{candidate.id}",
                roles=candidate.roles,
                auth_method="api_key",
                scopes=candidate.scopes,
                api_key_id=candidate.id,
            )
            return principal, tenant
        raise AuthenticationError("Invalid API key")

    async def _from_jwt(self, uow: UnitOfWork, token: str) -> tuple[Principal, Tenant]:
        if self._jwt is None:
            raise AuthenticationError("JWT authentication is not configured")
        claims = self._jwt.validate(token)
        tenant = await self._load_tenant(uow, TenantId(claims.tenant_id))
        parsed_roles = _parse_roles(claims.roles)
        principal = Principal(
            tenant_id=TenantId(claims.tenant_id),
            subject=claims.subject,
            roles=parsed_roles,
            auth_method="jwt",
            user_id=UserId(claims.subject),
            scopes=frozenset(claims.scopes),
            claims={k: str(v) for k, v in claims.raw.items() if isinstance(v, str | int)},
        )
        return principal, tenant

    @staticmethod
    async def _load_tenant(uow: UnitOfWork, tenant_id: TenantId) -> Tenant:
        tenant = await uow.tenants.get(tenant_id)
        if tenant is None:
            raise AuthenticationError("Tenant not found", details={"tenant_id": tenant_id})
        return tenant


__all__ = ["Authenticator"]
