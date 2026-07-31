"""Provider identity and health value objects."""

from __future__ import annotations

from enum import StrEnum


class ProviderName(StrEnum):
    """Canonical identifiers for supported upstream providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    AZURE_OPENAI = "azure_openai"
    BEDROCK = "bedrock"
    ECHO = "echo"
    """Deterministic in-process provider used for tests and local development."""

    @classmethod
    def parse(cls, raw: str) -> ProviderName:
        """Parse a provider identifier, accepting common aliases.

        Args:
            raw: Case-insensitive provider identifier.

        Returns:
            The matching :class:`ProviderName`.

        Raises:
            ValueError: If the identifier is unknown.
        """
        normalised = raw.strip().lower().replace("-", "_")
        aliases = {
            "azure": cls.AZURE_OPENAI,
            "aws": cls.BEDROCK,
            "aws_bedrock": cls.BEDROCK,
            "gemini": cls.GOOGLE,
            "google_gemini": cls.GOOGLE,
            "vertex": cls.GOOGLE,
        }
        if normalised in aliases:
            return aliases[normalised]
        return cls(normalised)


class ProviderStatus(StrEnum):
    """Operational status of a provider as observed by the gateway."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

    @property
    def is_routable(self) -> bool:
        """Return ``True`` when the provider may still receive traffic."""
        return self is not ProviderStatus.UNAVAILABLE


__all__ = ["ProviderName", "ProviderStatus"]
