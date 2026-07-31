"""PII detection and redaction domain service."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class PiiCategory(StrEnum):
    """Classes of personally identifiable information recognised by the gateway."""

    EMAIL = "email"
    PHONE = "phone"
    CREDIT_CARD = "credit_card"
    SSN = "ssn"
    IP_ADDRESS = "ip_address"
    IBAN = "iban"
    API_CREDENTIAL = "api_credential"
    JWT = "jwt"


_PATTERNS: Final[dict[PiiCategory, re.Pattern[str]]] = {
    PiiCategory.EMAIL: re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    PiiCategory.PHONE: re.compile(
        r"\b(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]?\d{4}\b"
    ),
    PiiCategory.CREDIT_CARD: re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    PiiCategory.SSN: re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    PiiCategory.IP_ADDRESS: re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    PiiCategory.IBAN: re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    PiiCategory.API_CREDENTIAL: re.compile(
        r"\b(?:sk|pk|rk|api|key|token|secret)[-_][A-Za-z0-9_\-]{16,}\b", re.IGNORECASE
    ),
    PiiCategory.JWT: re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
}

_LUHN_MIN_DIGITS: Final = 13
_LUHN_MAX_DIGITS: Final = 19
_MAX_SINGLE_DIGIT: Final = 9


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """The outcome of redacting a piece of text.

    Attributes:
        text: The redacted text.
        categories: Categories that were detected and masked.
        count: Total number of masked spans.
    """

    text: str
    categories: frozenset[PiiCategory] = frozenset()
    count: int = 0

    @property
    def redacted(self) -> bool:
        """Return ``True`` when at least one span was masked."""
        return self.count > 0


def _is_luhn_valid(digits: str) -> bool:
    """Validate a candidate card number using the Luhn checksum.

    Args:
        digits: Digit-only string.

    Returns:
        ``True`` when the checksum is valid and the length is plausible.
    """
    if not _LUHN_MIN_DIGITS <= len(digits) <= _LUHN_MAX_DIGITS:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > _MAX_SINGLE_DIGIT:
                value -= 9
        total += value
    return total % 10 == 0


class PiiRedactor:
    """Masks personally identifiable information in free text.

    The redactor is conservative on credit-card candidates: a digit run is only masked
    when it passes the Luhn checksum, which keeps order numbers and identifiers intact.
    """

    def __init__(
        self,
        *,
        categories: Iterable[PiiCategory] | None = None,
        placeholder: str = "[REDACTED:{category}]",
    ) -> None:
        """Initialise the redactor.

        Args:
            categories: Categories to detect; all categories when omitted.
            placeholder: Format string used for the replacement token.
        """
        self._categories = tuple(categories) if categories is not None else tuple(PiiCategory)
        self._placeholder = placeholder

    def detect(self, text: str) -> frozenset[PiiCategory]:
        """Detect which PII categories are present.

        Args:
            text: Text to scan.

        Returns:
            The detected categories.
        """
        found: set[PiiCategory] = set()
        for category in self._categories:
            for match in _PATTERNS[category].finditer(text):
                if self._is_true_positive(category, match.group(0)):
                    found.add(category)
                    break
        return frozenset(found)

    def redact(self, text: str) -> RedactionResult:
        """Mask every detected PII span.

        Args:
            text: Text to redact.

        Returns:
            The redaction result.
        """
        if not text:
            return RedactionResult(text=text)

        found: set[PiiCategory] = set()
        count = 0
        redacted = text
        for category in self._categories:
            redacted, hits = self._mask_category(redacted, category)
            if hits:
                found.add(category)
                count += hits
        return RedactionResult(text=redacted, categories=frozenset(found), count=count)

    def _mask_category(self, text: str, category: PiiCategory) -> tuple[str, int]:
        """Mask every true-positive span of one category.

        Args:
            text: Text to scan.
            category: Category to mask.

        Returns:
            The masked text and the number of spans replaced.
        """
        placeholder = self._placeholder.format(category=category.value.upper())
        pieces: list[str] = []
        cursor = 0
        hits = 0
        for match in _PATTERNS[category].finditer(text):
            if not self._is_true_positive(category, match.group(0)):
                continue
            pieces.append(text[cursor : match.start()])
            pieces.append(placeholder)
            cursor = match.end()
            hits += 1
        if hits == 0:
            return text, 0
        pieces.append(text[cursor:])
        return "".join(pieces), hits

    @staticmethod
    def _is_true_positive(category: PiiCategory, value: str) -> bool:
        if category is PiiCategory.CREDIT_CARD:
            return _is_luhn_valid(re.sub(r"\D", "", value))
        return True


__all__ = ["PiiCategory", "PiiRedactor", "RedactionResult"]
