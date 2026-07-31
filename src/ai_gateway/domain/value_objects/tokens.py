"""Token accounting value object."""

from __future__ import annotations

from dataclasses import dataclass

from ai_gateway.domain.errors import ValidationError


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts reported by (or estimated for) a provider call.

    Attributes:
        prompt_tokens: Tokens consumed by the request payload.
        completion_tokens: Tokens produced by the model.
        cached_prompt_tokens: Prompt tokens served from a provider-side cache.
        reasoning_tokens: Hidden reasoning tokens billed by some providers.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        """Validate that no counter is negative.

        Raises:
            ValidationError: If any counter is negative.
        """
        negatives = {
            name: value
            for name, value in self.as_dict().items()
            if isinstance(value, int) and value < 0
        }
        if negatives:
            raise ValidationError("Token counts must not be negative", details=negatives)

    @property
    def total_tokens(self) -> int:
        """Return the billable total across prompt, completion and reasoning tokens."""
        return self.prompt_tokens + self.completion_tokens + self.reasoning_tokens

    @property
    def billable_prompt_tokens(self) -> int:
        """Return prompt tokens excluding those served from a provider cache."""
        return max(self.prompt_tokens - self.cached_prompt_tokens, 0)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Accumulate two usage records."""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_prompt_tokens=self.cached_prompt_tokens + other.cached_prompt_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def as_dict(self) -> dict[str, int]:
        """Return a serialisable mapping of the counters.

        Returns:
            Mapping of counter name to value, including the derived total.
        """
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens + self.reasoning_tokens,
        }

    @classmethod
    def empty(cls) -> TokenUsage:
        """Return an all-zero usage record."""
        return cls()


__all__ = ["TokenUsage"]
