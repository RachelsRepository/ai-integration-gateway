"""Secret resolvers.

Supported reference schemes:

* ``env://NAME`` — process environment variable
* ``file:///absolute/path`` — file contents
* ``literal://value`` — development-only inline value
* bare strings — treated as literals outside production
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from ai_gateway.domain.errors import ValidationError


class CompositeSecretResolver:
    """Resolves secret references from the environment, filesystem or literals."""

    def __init__(self, *, allow_literals: bool = True, cache: bool = True) -> None:
        """Initialise the resolver.

        Args:
            allow_literals: Whether bare / ``literal://`` values are accepted.
            cache: Whether resolved values are cached for the process lifetime.
        """
        self._allow_literals = allow_literals
        self._cache_enabled = cache
        self._cache: dict[str, str] = {}

    async def resolve(self, reference: str) -> str:
        """Resolve a secret reference.

        Args:
            reference: Secret reference.

        Returns:
            The plaintext secret.

        Raises:
            ValidationError: If the reference is malformed or cannot be resolved.
        """
        if self._cache_enabled and reference in self._cache:
            return self._cache[reference]
        value = self._resolve(reference)
        if self._cache_enabled:
            self._cache[reference] = value
        return value

    async def resolve_optional(self, reference: str | None) -> str | None:
        """Resolve a reference that may be absent.

        Args:
            reference: Secret reference or ``None``.

        Returns:
            The plaintext secret, or ``None``.
        """
        if reference is None or reference == "":
            return None
        return await self.resolve(reference)

    def invalidate(self, reference: str) -> None:
        """Drop a cached secret.

        Args:
            reference: The reference to invalidate.
        """
        self._cache.pop(reference, None)

    def _resolve(self, reference: str) -> str:
        if "://" not in reference:
            if not self._allow_literals:
                raise ValidationError(
                    "Bare secret literals are not permitted in this environment",
                    details={"reference": reference[:32]},
                )
            return reference

        parsed = urlparse(reference)
        scheme = parsed.scheme
        if scheme == "env":
            name = parsed.netloc or parsed.path.lstrip("/")
            value = os.environ.get(name)
            if value is None:
                raise ValidationError("Environment secret is not set", details={"name": name})
            return value
        if scheme == "file":
            path = Path(unquote(parsed.path))
            if not path.is_file():
                raise ValidationError("Secret file does not exist", details={"path": str(path)})
            return path.read_text(encoding="utf-8").strip()
        if scheme == "literal":
            if not self._allow_literals:
                raise ValidationError("literal:// secrets are not permitted")
            return unquote(parsed.netloc + parsed.path)
        raise ValidationError("Unsupported secret reference scheme", details={"scheme": scheme})


__all__ = ["CompositeSecretResolver"]
