"""Guardrail service: input screening, redaction and output filtering."""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_gateway.domain.entities.message import Message, MessageRole
from ai_gateway.domain.entities.tenant import Tenant
from ai_gateway.domain.services.content_safety import (
    InjectionVerdict,
    OutputFilter,
    PromptInjectionDetector,
    RiskLevel,
)
from ai_gateway.domain.services.redaction import PiiCategory, PiiRedactor


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    """The result of screening an inbound request.

    Attributes:
        messages: Messages after redaction, ready to send upstream.
        injection: Injection screening verdict.
        redacted_categories: PII categories masked before egress.
        redaction_count: Number of masked spans.
    """

    messages: tuple[Message, ...]
    injection: InjectionVerdict
    redacted_categories: frozenset[PiiCategory] = frozenset()
    redaction_count: int = 0
    annotations: dict[str, str] = field(default_factory=dict)

    @property
    def modified(self) -> bool:
        """Return ``True`` when the request was altered by redaction."""
        return self.redaction_count > 0


class GuardrailService:
    """Applies tenant-configurable safety controls to requests and responses.

    Only untrusted content is screened. System and safety instructions authored by the
    tenant are trusted and are neither redacted nor scanned, which prevents the detector
    from firing on the platform's own guardrail wording.
    """

    def __init__(
        self,
        *,
        detector: PromptInjectionDetector | None = None,
        redactor: PiiRedactor | None = None,
        output_filter: OutputFilter | None = None,
        block_threshold: RiskLevel = RiskLevel.HIGH,
    ) -> None:
        """Initialise the service.

        Args:
            detector: Prompt injection detector.
            redactor: PII redactor.
            output_filter: Output filter applied to completions.
            block_threshold: Minimum injection severity that blocks a request.
        """
        self._detector = detector or PromptInjectionDetector(block_threshold=block_threshold)
        self._redactor = redactor or PiiRedactor()
        self._output_filter = output_filter or OutputFilter()
        self._block_threshold = block_threshold

    def screen_request(self, messages: tuple[Message, ...], tenant: Tenant) -> GuardrailVerdict:
        """Screen and sanitise an inbound transcript.

        Args:
            messages: Caller-supplied transcript.
            tenant: Tenant whose policy applies.

        Returns:
            The verdict, including the possibly redacted transcript.

        Raises:
            PromptInjectionError: If injection screening blocks the request.
        """
        untrusted = [m.content for m in messages if m.role in _UNTRUSTED_ROLES]
        verdict = (
            self._detector.inspect_all(untrusted)
            if tenant.injection_detection_enabled
            else InjectionVerdict()
        )
        if tenant.injection_detection_enabled:
            verdict.raise_if_blocked(threshold=self._block_threshold)

        if not tenant.pii_redaction_enabled:
            return GuardrailVerdict(messages=messages, injection=verdict)

        sanitised: list[Message] = []
        categories: set[PiiCategory] = set()
        count = 0
        for message in messages:
            if message.role not in _UNTRUSTED_ROLES or not message.content:
                sanitised.append(message)
                continue
            result = self._redactor.redact(message.content)
            categories |= result.categories
            count += result.count
            sanitised.append(message.with_content(result.text) if result.redacted else message)

        return GuardrailVerdict(
            messages=tuple(sanitised),
            injection=verdict,
            redacted_categories=frozenset(categories),
            redaction_count=count,
        )

    def screen_tool_output(self, output: str) -> InjectionVerdict:
        """Screen tool output before it re-enters the model context.

        Tool results are the most common indirect prompt-injection vector, so they are
        treated exactly like user input.

        Args:
            output: Serialised tool result.

        Returns:
            The screening verdict.
        """
        return self._detector.inspect(output)

    def filter_output(self, content: str) -> str:
        """Sanitise a completion before it leaves the gateway.

        Args:
            content: Raw model output.

        Returns:
            The filtered output.
        """
        return self._output_filter.enforce(content)


_UNTRUSTED_ROLES = frozenset({MessageRole.USER, MessageRole.TOOL})

__all__ = ["GuardrailService", "GuardrailVerdict"]
