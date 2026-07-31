"""Prompt template aggregate with immutable versioning."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai_gateway.domain.entities.message import Message, MessageRole
from ai_gateway.domain.errors import ConflictError, NotFoundError, PromptValidationError
from ai_gateway.domain.value_objects.identifiers import PromptId, TenantId, new_id

_VARIABLE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")
_MAX_TEMPLATE_CHARS = 100_000


def extract_variables(template: str) -> frozenset[str]:
    """Extract ``{{ variable }}`` placeholders from a template body.

    Args:
        template: Raw template text.

    Returns:
        The set of referenced variable names.
    """
    return frozenset(_VARIABLE_PATTERN.findall(template))


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """The result of rendering a prompt version.

    Attributes:
        messages: Fully substituted messages ready for provider translation.
        prompt_id: Source prompt identifier.
        version: Source prompt version number.
        variables: Variable values used during rendering.
    """

    messages: tuple[Message, ...]
    prompt_id: PromptId
    version: int
    variables: dict[str, str] = field(default_factory=dict)

    def as_messages(self) -> list[Message]:
        """Return the rendered messages as a mutable list."""
        return list(self.messages)


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """An immutable, published revision of a prompt template.

    Attributes:
        version: Monotonically increasing revision number.
        template: User-facing template body containing ``{{ variable }}`` placeholders.
        system_prompt: Optional system instruction prepended to the rendered messages.
        safety_prompt: Optional guardrail instruction appended after the system prompt.
        required_variables: Variables that must be supplied at render time.
        created_at: Publication timestamp in UTC.
        created_by: Identifier of the principal that published the revision.
        notes: Change description recorded for audit purposes.
    """

    version: int
    template: str
    system_prompt: str | None = None
    safety_prompt: str | None = None
    required_variables: frozenset[str] = frozenset()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_by: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        """Validate the revision.

        Raises:
            PromptValidationError: If the body is empty, oversized or references
                variables that are not declared as required.
        """
        if not self.template.strip():
            raise PromptValidationError("Prompt template body must not be empty")
        if len(self.template) > _MAX_TEMPLATE_CHARS:
            raise PromptValidationError(
                "Prompt template exceeds maximum size",
                details={"max_chars": _MAX_TEMPLATE_CHARS, "actual": len(self.template)},
            )
        if self.version < 1:
            raise PromptValidationError("Prompt version must start at 1")
        declared = self.required_variables
        referenced = extract_variables(self.template)
        undeclared = referenced - declared
        if undeclared:
            raise PromptValidationError(
                "Template references variables that are not declared",
                details={"undeclared": sorted(undeclared)},
            )

    def render(self, variables: dict[str, Any] | None = None) -> tuple[Message, ...]:
        """Substitute variables and build the message list.

        Args:
            variables: Values for the declared variables.

        Returns:
            The rendered messages, ordered system, safety, then user content.

        Raises:
            PromptValidationError: If a required variable is missing or a value is not a
                primitive that can be safely stringified.
        """
        supplied = variables or {}
        missing = sorted(self.required_variables - set(supplied))
        if missing:
            raise PromptValidationError(
                "Missing required prompt variables", details={"missing": missing}
            )
        coerced: dict[str, str] = {}
        for key, value in supplied.items():
            if isinstance(value, str | int | float | bool):
                coerced[key] = str(value)
            else:
                raise PromptValidationError(
                    "Prompt variables must be primitive values",
                    details={"variable": key, "type": type(value).__name__},
                )

        body = _VARIABLE_PATTERN.sub(lambda m: coerced.get(m.group(1), ""), self.template)

        messages: list[Message] = []
        if self.system_prompt:
            messages.append(Message(role=MessageRole.SYSTEM, content=self.system_prompt))
        if self.safety_prompt:
            messages.append(Message(role=MessageRole.SYSTEM, content=self.safety_prompt))
        messages.append(Message(role=MessageRole.USER, content=body))
        return tuple(messages)


@dataclass(slots=True)
class PromptTemplate:
    """A named, versioned prompt owned by a tenant.

    Attributes:
        tenant_id: Owning tenant.
        name: Stable, URL-safe prompt name, unique per tenant.
        id: Stable prompt identifier.
        description: Human readable description.
        versions: Published revisions, ordered by version number.
        active_version: Version served when no explicit version is requested.
        labels: Free-form classification labels.
        created_at: Creation timestamp in UTC.
        updated_at: Timestamp of the most recent publication.
    """

    tenant_id: TenantId
    name: str
    id: PromptId = field(default_factory=lambda: PromptId(new_id()))
    description: str | None = None
    versions: list[PromptVersion] = field(default_factory=list)
    active_version: int | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Validate the prompt name.

        Raises:
            PromptValidationError: If the name is not URL-safe.
        """
        if not _NAME_PATTERN.match(self.name):
            raise PromptValidationError(
                "Prompt name must be lowercase, 3-64 chars, and use only [a-z0-9._-]",
                details={"name": self.name},
            )

    def publish(
        self,
        *,
        template: str,
        system_prompt: str | None = None,
        safety_prompt: str | None = None,
        required_variables: frozenset[str] | None = None,
        created_by: str | None = None,
        notes: str | None = None,
        activate: bool = True,
        now: datetime | None = None,
    ) -> PromptVersion:
        """Publish a new immutable revision.

        Args:
            template: Template body.
            system_prompt: Optional system instruction.
            safety_prompt: Optional guardrail instruction.
            required_variables: Declared variables; inferred from the body when omitted.
            created_by: Publishing principal.
            notes: Change description.
            activate: Whether the new revision becomes the active version.
            now: Injected clock value.

        Returns:
            The published revision.
        """
        timestamp = now or datetime.now(UTC)
        version = PromptVersion(
            version=self.next_version,
            template=template,
            system_prompt=system_prompt,
            safety_prompt=safety_prompt,
            required_variables=(
                required_variables
                if required_variables is not None
                else extract_variables(template)
            ),
            created_at=timestamp,
            created_by=created_by,
            notes=notes,
        )
        self.versions.append(version)
        if activate:
            self.active_version = version.version
        self.updated_at = timestamp
        return version

    @property
    def next_version(self) -> int:
        """Return the version number that the next publication will receive."""
        return max((v.version for v in self.versions), default=0) + 1

    def get_version(self, version: int | None = None) -> PromptVersion:
        """Resolve a specific revision.

        Args:
            version: Revision to fetch; the active revision when omitted.

        Returns:
            The requested revision.

        Raises:
            ConflictError: If no revision has been published or activated.
            NotFoundError: If the requested revision does not exist.
        """
        target = version if version is not None else self.active_version
        if target is None:
            raise ConflictError("Prompt has no active version", details={"prompt": self.name})
        for candidate in self.versions:
            if candidate.version == target:
                return candidate
        raise NotFoundError(
            "Prompt version not found", details={"prompt": self.name, "version": target}
        )

    def activate(self, version: int) -> None:
        """Promote an existing revision to active.

        Args:
            version: Revision to activate.
        """
        self.get_version(version)
        self.active_version = version

    def render(
        self, variables: dict[str, Any] | None = None, *, version: int | None = None
    ) -> RenderedPrompt:
        """Render a revision into messages.

        Args:
            variables: Values for the declared variables.
            version: Revision to render; the active revision when omitted.

        Returns:
            The rendered prompt.
        """
        resolved = self.get_version(version)
        messages = resolved.render(variables)
        return RenderedPrompt(
            messages=messages,
            prompt_id=self.id,
            version=resolved.version,
            variables={k: str(v) for k, v in (variables or {}).items()},
        )

    def history(self) -> list[PromptVersion]:
        """Return every published revision, newest first."""
        return sorted(self.versions, key=lambda v: v.version, reverse=True)


__all__ = ["PromptTemplate", "PromptVersion", "RenderedPrompt", "extract_variables"]
