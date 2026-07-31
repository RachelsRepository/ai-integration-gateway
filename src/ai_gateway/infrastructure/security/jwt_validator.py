"""JWT validation against a JWKS endpoint or a shared secret."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from ai_gateway.domain.errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class JwtClaims:
    """Validated JWT claims.

    Attributes:
        subject: Subject identifier.
        tenant_id: Tenant claim.
        roles: Roles claim.
        scopes: OAuth2 scopes.
        raw: Full claim set.
    """

    subject: str
    tenant_id: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    raw: dict[str, Any]


class JwtValidator:
    """Validates bearer tokens."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        algorithms: tuple[str, ...] = ("RS256",),
        jwks_url: str | None = None,
        shared_secret: str | None = None,
        tenant_claim: str = "tenant_id",
        roles_claim: str = "roles",
        scope_claim: str = "scope",
        leeway_seconds: int = 30,
    ) -> None:
        """Initialise the validator.

        Args:
            issuer: Expected issuer.
            audience: Expected audience.
            algorithms: Allowed algorithms.
            jwks_url: JWKS endpoint for asymmetric keys.
            shared_secret: Shared secret for HS* algorithms.
            tenant_claim: Claim carrying the tenant identifier.
            roles_claim: Claim carrying roles.
            scope_claim: Claim carrying scopes.
            leeway_seconds: Clock skew tolerance.
        """
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._jwks = PyJWKClient(jwks_url, cache_keys=True) if jwks_url else None
        self._shared_secret = shared_secret
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._scope_claim = scope_claim
        self._leeway = leeway_seconds

    def validate(self, token: str) -> JwtClaims:
        """Validate a bearer token and extract claims.

        Args:
            token: Compact JWT.

        Returns:
            The validated claims.

        Raises:
            AuthenticationError: If the token is invalid.
        """
        try:
            key: Any
            if self._jwks is not None:
                key = self._jwks.get_signing_key_from_jwt(token).key
            elif self._shared_secret is not None:
                key = self._shared_secret
            else:
                raise AuthenticationError("JWT validator is not configured")
            payload = jwt.decode(
                token,
                key=key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(
                "JWT validation failed", details={"reason": str(exc)}
            ) from exc

        subject = str(payload.get("sub", ""))
        tenant_id = str(payload.get(self._tenant_claim, ""))
        if not subject or not tenant_id:
            raise AuthenticationError("JWT is missing required claims")
        roles = payload.get(self._roles_claim, [])
        if isinstance(roles, str):
            roles = [roles]
        scopes_raw = payload.get(self._scope_claim, [])
        if isinstance(scopes_raw, str):
            scopes = tuple(scopes_raw.split())
        else:
            scopes = tuple(str(s) for s in scopes_raw)
        return JwtClaims(
            subject=subject,
            tenant_id=tenant_id,
            roles=tuple(str(r) for r in roles),
            scopes=scopes,
            raw=dict(payload),
        )


__all__ = ["JwtClaims", "JwtValidator"]
