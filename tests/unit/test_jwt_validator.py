"""JWT validator tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from ai_gateway.domain.errors import AuthenticationError
from ai_gateway.infrastructure.security.jwt_validator import JwtValidator

JWT_SECRET = "test-secret-key-thirty-two-bytes-long!!"


def _mint(
    *,
    secret: str = JWT_SECRET,
    sub: str = "user-1",
    tenant_id: str = "tenant-1",
    roles: list[str] | tuple[str, ...] | str = ("admin",),
    scope: list[str] | str = "chat:read chat:write",
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "tenant_id": tenant_id,
        "roles": roles,
        "scope": scope,
        "iss": "https://issuer.test",
        "aud": "gateway",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def test_jwt_validator_accepts_hs256_token() -> None:
    token = _mint()
    validator = JwtValidator(
        issuer="https://issuer.test",
        audience="gateway",
        algorithms=("HS256",),
        shared_secret=JWT_SECRET,
    )
    claims = validator.validate(token)
    assert claims.subject == "user-1"
    assert claims.tenant_id == "tenant-1"
    assert claims.roles == ("admin",)
    assert "chat:read" in claims.scopes


def test_jwt_validator_rejects_bad_token() -> None:
    validator = JwtValidator(
        issuer="https://issuer.test",
        audience="gateway",
        algorithms=("HS256",),
        shared_secret=JWT_SECRET,
    )
    with pytest.raises(AuthenticationError):
        validator.validate("not.a.token")


def test_jwt_validator_requires_configuration() -> None:
    validator = JwtValidator(issuer="iss", audience="aud")
    with pytest.raises(AuthenticationError, match="not configured"):
        validator.validate(_mint())


def test_jwt_validator_missing_claims() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "",
            "iss": "https://issuer.test",
            "aud": "gateway",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    validator = JwtValidator(
        issuer="https://issuer.test",
        audience="gateway",
        algorithms=("HS256",),
        shared_secret=JWT_SECRET,
    )
    with pytest.raises(AuthenticationError, match="missing required claims"):
        validator.validate(token)
