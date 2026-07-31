"""Prompt injection detection and output filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ai_gateway.domain.errors import ContentPolicyError, PromptInjectionError


class RiskLevel(StrEnum):
    """Severity assigned to a detected content risk."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class InjectionVerdict:
    """The result of screening text for prompt-injection attempts.

    Attributes:
        risk: Highest severity matched.
        score: Normalised risk score in ``[0, 1]``.
        signals: Names of the heuristics that fired.
    """

    risk: RiskLevel = RiskLevel.NONE
    score: float = 0.0
    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_suspicious(self) -> bool:
        """Return ``True`` when any heuristic fired."""
        return self.risk is not RiskLevel.NONE

    def raise_if_blocked(self, *, threshold: RiskLevel = RiskLevel.HIGH) -> None:
        """Reject the request when the verdict meets or exceeds a threshold.

        Args:
            threshold: Minimum severity that causes rejection.

        Raises:
            PromptInjectionError: If the verdict is at or above the threshold.
        """
        if _SEVERITY[self.risk] >= _SEVERITY[threshold] and self.is_suspicious:
            raise PromptInjectionError(
                "Prompt injection heuristics rejected the request",
                details={"risk": self.risk.value, "signals": list(self.signals)},
            )


_SEVERITY: Final[dict[RiskLevel, int]] = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
}

_INJECTION_SIGNALS: Final[tuple[tuple[str, re.Pattern[str], RiskLevel], ...]] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b"
            r"(previous|prior|above|earlier|all)\b[^.\n]{0,20}\b"
            r"(instruction|instructions|prompt|prompts|rule|rules|context)\b",
            re.IGNORECASE,
        ),
        RiskLevel.HIGH,
    ),
    (
        "system_prompt_exfiltration",
        re.compile(
            r"\b(reveal|show|print|repeat|output|disclose|dump)\b[^.\n]{0,40}\b"
            r"(system prompt|initial instructions|your instructions|hidden prompt|"
            r"developer message)\b",
            re.IGNORECASE,
        ),
        RiskLevel.HIGH,
    ),
    (
        "role_hijack",
        re.compile(
            r"(^|\n)\s*(system|developer)\s*:\s*|<\s*/?\s*(system|im_start|im_end)\s*>",
            re.IGNORECASE,
        ),
        RiskLevel.HIGH,
    ),
    (
        "guardrail_bypass",
        re.compile(
            r"\b(developer mode|jailbreak|dan mode|do anything now|"
            r"without any restrictions|bypass (the )?(safety|filter|guardrails?))\b",
            re.IGNORECASE,
        ),
        RiskLevel.HIGH,
    ),
    (
        "credential_harvesting",
        re.compile(
            r"\b(api[ _-]?key|access[ _-]?token|secret[ _-]?key|password|credential)s?\b"
            r"[^.\n]{0,30}\b(show|reveal|print|give|send|leak)\b",
            re.IGNORECASE,
        ),
        RiskLevel.MEDIUM,
    ),
    (
        "tool_abuse",
        re.compile(
            r"\b(call|invoke|execute|run)\b[^.\n]{0,25}\b(every|all)\b[^.\n]{0,15}\btools?\b",
            re.IGNORECASE,
        ),
        RiskLevel.MEDIUM,
    ),
    (
        "encoded_payload",
        re.compile(
            r"\b(base64|rot13|hex[- ]?encoded)\b[^.\n]{0,30}\b(decode|execute|run)\b", re.IGNORECASE
        ),
        RiskLevel.LOW,
    ),
    (
        "excessive_delimiters",
        re.compile(r"(-{10,}|={10,}|`{6,})"),
        RiskLevel.LOW,
    ),
)

_SCORE_BY_RISK: Final[dict[RiskLevel, float]] = {
    RiskLevel.NONE: 0.0,
    RiskLevel.LOW: 0.25,
    RiskLevel.MEDIUM: 0.6,
    RiskLevel.HIGH: 0.95,
}


class PromptInjectionDetector:
    """Screens untrusted text for prompt-injection and guardrail-bypass attempts.

    Detection is heuristic and layered with other controls: the gateway also isolates
    system instructions from user content and constrains tool permissions. The detector
    reports a verdict rather than mutating the prompt so that callers can choose between
    blocking, flagging or downgrading the request.
    """

    def __init__(self, *, block_threshold: RiskLevel = RiskLevel.HIGH) -> None:
        """Initialise the detector.

        Args:
            block_threshold: Minimum severity that should block a request.
        """
        self.block_threshold = block_threshold

    def inspect(self, text: str) -> InjectionVerdict:
        """Screen a single piece of text.

        Args:
            text: Untrusted text supplied by a caller or a tool.

        Returns:
            The screening verdict.
        """
        if not text:
            return InjectionVerdict()
        signals: list[str] = []
        highest = RiskLevel.NONE
        for name, pattern, level in _INJECTION_SIGNALS:
            if pattern.search(text):
                signals.append(name)
                if _SEVERITY[level] > _SEVERITY[highest]:
                    highest = level
        score = min(_SCORE_BY_RISK[highest] + 0.05 * max(len(signals) - 1, 0), 1.0)
        return InjectionVerdict(risk=highest, score=score, signals=tuple(signals))

    def inspect_all(self, texts: list[str]) -> InjectionVerdict:
        """Screen several pieces of text and return the most severe verdict.

        Args:
            texts: Untrusted texts.

        Returns:
            The aggregate verdict.
        """
        verdicts = [self.inspect(text) for text in texts]
        if not verdicts:
            return InjectionVerdict()
        worst = max(verdicts, key=lambda v: (_SEVERITY[v.risk], v.score))
        merged = tuple(dict.fromkeys(signal for v in verdicts for signal in v.signals))
        return InjectionVerdict(risk=worst.risk, score=worst.score, signals=merged)

    def enforce(self, text: str) -> InjectionVerdict:
        """Screen text and block it when it exceeds the configured threshold.

        Args:
            text: Untrusted text.

        Returns:
            The verdict when the text is permitted.

        Raises:
            PromptInjectionError: If the verdict exceeds the block threshold.
        """
        verdict = self.inspect(text)
        verdict.raise_if_blocked(threshold=self.block_threshold)
        return verdict


_SECRET_LEAK_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}=*", re.IGNORECASE)),
    ("connection_string", re.compile(r"\b\w+://[^\s:@/]+:[^\s:@/]+@[^\s/]+", re.IGNORECASE)),
)


@dataclass(frozen=True, slots=True)
class FilterResult:
    """The outcome of filtering model output.

    Attributes:
        text: The filtered text.
        blocked: Whether the output should be withheld entirely.
        findings: Names of the filters that fired.
    """

    text: str
    blocked: bool = False
    findings: tuple[str, ...] = field(default_factory=tuple)


class OutputFilter:
    """Sanitises model output before it leaves the gateway.

    The filter removes credential-shaped strings that a model may have reproduced from
    its context and optionally blocks responses outright.
    """

    def __init__(self, *, block_on_secret: bool = False, placeholder: str = "[REDACTED]") -> None:
        """Initialise the filter.

        Args:
            block_on_secret: Whether a detected secret should block the whole response.
            placeholder: Replacement token for masked spans.
        """
        self._block_on_secret = block_on_secret
        self._placeholder = placeholder

    def filter(self, text: str) -> FilterResult:
        """Sanitise a model completion.

        Args:
            text: Raw model output.

        Returns:
            The filter result.
        """
        if not text:
            return FilterResult(text=text)
        findings: list[str] = []
        filtered = text
        for name, pattern in _SECRET_LEAK_PATTERNS:
            filtered, replacements = pattern.subn(self._placeholder, filtered)
            if replacements:
                findings.append(name)
        blocked = bool(findings) and self._block_on_secret
        return FilterResult(text=filtered, blocked=blocked, findings=tuple(findings))

    def enforce(self, text: str) -> str:
        """Sanitise output and raise when the response must be withheld.

        Args:
            text: Raw model output.

        Returns:
            The sanitised output.

        Raises:
            ContentPolicyError: If the response is blocked by policy.
        """
        result = self.filter(text)
        if result.blocked:
            raise ContentPolicyError(
                "Model output withheld by content policy",
                details={"findings": list(result.findings)},
            )
        return result.text


__all__ = [
    "FilterResult",
    "InjectionVerdict",
    "OutputFilter",
    "PromptInjectionDetector",
    "RiskLevel",
]
