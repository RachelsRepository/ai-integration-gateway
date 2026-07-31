"""Secret management port.

Credentials are never read from the domain or application layers directly. Adapters
resolve references such as ``secretsmanager://prod/openai#api_key`` at start-up and on
rotation, so the process never holds a plaintext secret in configuration.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretResolver(Protocol):
    """Resolves secret references to plaintext values."""

    async def resolve(self, reference: str) -> str:
        """Resolve a secret reference.

        Args:
            reference: A URI-style secret reference, or a literal value in development.

        Returns:
            The plaintext secret.
        """
        ...

    async def resolve_optional(self, reference: str | None) -> str | None:
        """Resolve a reference that may be absent.

        Args:
            reference: A secret reference or ``None``.

        Returns:
            The plaintext secret, or ``None`` when no reference was supplied.
        """
        ...

    def invalidate(self, reference: str) -> None:
        """Drop a cached secret so the next resolution refetches it.

        Args:
            reference: The reference to invalidate.
        """
        ...


__all__ = ["SecretResolver"]
