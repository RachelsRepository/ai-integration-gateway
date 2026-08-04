"""API key hashing and verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from ai_gateway.domain.entities.tenant import ApiKey, Role
from ai_gateway.domain.value_objects.identifiers import ApiKeyId, TenantId, new_id


class ApiKeyHasher:
    """Hashes and verifies API keys with HMAC-SHA256 and a server-side pepper."""

    def __init__(self, pepper: str, *, prefix_length: int = 8) -> None:
        """Initialise the hasher.

        Args:
            pepper: Server-side pepper mixed into every hash.
            prefix_length: Non-secret prefix length retained for lookup.
        """
        self._pepper = pepper.encode("utf-8")
        self._prefix_length = prefix_length

    def generate(self) -> tuple[str, str, str]:
        """Generate a new plaintext key, its prefix and its hash.

        Returns:
            A tuple of ``(plaintext, prefix, hashed_secret)``.
        """
        plaintext = f"aigw_{secrets.token_urlsafe(32)}"
        prefix = plaintext[: self._prefix_length]
        return plaintext, prefix, self.hash(plaintext)

    def hash(self, plaintext: str) -> str:
        """Hash a plaintext key.

        Args:
            plaintext: The secret to hash.

        Returns:
            The hex-encoded HMAC digest.
        """
        return hmac.new(self._pepper, plaintext.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify(self, plaintext: str, hashed_secret: str) -> bool:
        """Constant-time verification of a plaintext key against a stored hash.

        Args:
            plaintext: Presented secret.
            hashed_secret: Stored hash.

        Returns:
            ``True`` when the secret matches.
        """
        return hmac.compare_digest(self.hash(plaintext), hashed_secret)

    def mint(
        self,
        *,
        tenant_id: TenantId,
        name: str = "default",
        roles: frozenset[Role] | None = None,
        plaintext: str | None = None,
    ) -> tuple[str, ApiKey]:
        """Mint a new credential for a tenant.

        Args:
            tenant_id: Owning tenant.
            name: Human readable label.
            roles: Roles granted to bearers.
            plaintext: Optional known secret (local demo only); otherwise generated.

        Returns:
            The plaintext secret (shown once) and the persistable entity.
        """
        if plaintext is None:
            plaintext, prefix, hashed = self.generate()
        else:
            if not plaintext.startswith("aigw_"):
                raise ValueError("API keys must start with aigw_")
            prefix = plaintext[: self._prefix_length]
            hashed = self.hash(plaintext)
        entity = ApiKey(
            tenant_id=tenant_id,
            prefix=prefix,
            hashed_secret=hashed,
            id=ApiKeyId(new_id()),
            name=name,
            roles=roles or frozenset({Role.SERVICE}),
        )
        return plaintext, entity

    def prefix_of(self, plaintext: str) -> str:
        """Return the non-secret prefix of a plaintext key.

        Args:
            plaintext: Presented secret.

        Returns:
            The lookup prefix.
        """
        return plaintext[: self._prefix_length]


__all__ = ["ApiKeyHasher"]
