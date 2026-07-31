"""Heuristic token estimation used for pre-flight checks."""

from __future__ import annotations

import re

from ai_gateway.domain.entities.message import Message

_WORD_PATTERN = re.compile(r"\w+|[^\w\s]")
_PER_MESSAGE_OVERHEAD = 4
_PER_REQUEST_OVERHEAD = 3
_TOKENS_PER_WORD = 1.35


class TokenEstimator:
    """Estimates prompt size without depending on a provider tokenizer.

    Exact tokenisation differs per provider and per model family. The gateway only needs a
    conservative estimate to enforce context windows, project cost and pick a route; the
    authoritative counts always come back from the provider response.
    """

    def __init__(self, *, tokens_per_word: float = _TOKENS_PER_WORD) -> None:
        """Initialise the estimator.

        Args:
            tokens_per_word: Multiplier applied to the word/punctuation count.
        """
        self._tokens_per_word = tokens_per_word

    def estimate_text(self, text: str) -> int:
        """Estimate the token count of a string.

        Args:
            text: Input text.

        Returns:
            The estimated token count.
        """
        if not text:
            return 0
        units = len(_WORD_PATTERN.findall(text))
        return max(int(units * self._tokens_per_word), 1)

    def estimate_message(self, message: Message) -> int:
        """Estimate the token count of one message including protocol overhead.

        Args:
            message: The message to measure.

        Returns:
            The estimated token count.
        """
        total = self.estimate_text(message.content) + _PER_MESSAGE_OVERHEAD
        for call in message.tool_calls:
            total += self.estimate_text(call.name) + self.estimate_text(str(call.arguments))
        if message.name:
            total += self.estimate_text(message.name)
        return total

    def estimate_messages(self, messages: list[Message]) -> int:
        """Estimate the token count of a full transcript.

        Args:
            messages: Ordered transcript.

        Returns:
            The estimated token count.
        """
        return sum(self.estimate_message(m) for m in messages) + _PER_REQUEST_OVERHEAD


__all__ = ["TokenEstimator"]
