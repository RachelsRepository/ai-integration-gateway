"""Correlation context propagated across the request lifecycle.

Context variables survive ``await`` boundaries within a task, so every log line, metric
exemplar and outbound header can carry the same correlation identifiers without threading
them through every function signature.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
principal_var: ContextVar[str | None] = ContextVar("principal", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """A snapshot of the current correlation identifiers.

    Attributes:
        request_id: Gateway request identifier.
        tenant_id: Tenant the request acts under.
        principal: Authenticated subject.
        trace_id: Distributed trace identifier.
    """

    request_id: str | None = None
    tenant_id: str | None = None
    principal: str | None = None
    trace_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        """Return the non-empty identifiers as a log-friendly mapping."""
        pairs = {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "principal": self.principal,
            "trace_id": self.trace_id,
        }
        return {key: value for key, value in pairs.items() if value}


@dataclass(slots=True)
class _Tokens:
    """Reset tokens returned by :func:`bind_context`."""

    request_id: Token[str | None]
    tenant_id: Token[str | None]
    principal: Token[str | None]
    trace_id: Token[str | None]


def new_request_id() -> str:
    """Generate a new request identifier.

    Returns:
        A canonical UUID4 string.
    """
    return str(uuid.uuid4())


def bind_context(
    *,
    request_id: str | None = None,
    tenant_id: str | None = None,
    principal: str | None = None,
    trace_id: str | None = None,
) -> _Tokens:
    """Bind correlation identifiers to the current context.

    Args:
        request_id: Gateway request identifier.
        tenant_id: Tenant the request acts under.
        principal: Authenticated subject.
        trace_id: Distributed trace identifier.

    Returns:
        Tokens that restore the previous values when passed to :func:`clear_context`.
    """
    return _Tokens(
        request_id=request_id_var.set(request_id or request_id_var.get()),
        tenant_id=tenant_id_var.set(tenant_id or tenant_id_var.get()),
        principal=principal_var.set(principal or principal_var.get()),
        trace_id=trace_id_var.set(trace_id or trace_id_var.get()),
    )


def clear_context(tokens: _Tokens | None = None) -> None:
    """Restore the previous correlation context.

    Args:
        tokens: Tokens returned by :func:`bind_context`; when omitted the context is
            reset to empty.
    """
    if tokens is None:
        request_id_var.set(None)
        tenant_id_var.set(None)
        principal_var.set(None)
        trace_id_var.set(None)
        return
    request_id_var.reset(tokens.request_id)
    tenant_id_var.reset(tokens.tenant_id)
    principal_var.reset(tokens.principal)
    trace_id_var.reset(tokens.trace_id)


def current_context() -> CorrelationContext:
    """Return the correlation identifiers bound to the current context.

    Returns:
        The current snapshot.
    """
    return CorrelationContext(
        request_id=request_id_var.get(),
        tenant_id=tenant_id_var.get(),
        principal=principal_var.get(),
        trace_id=trace_id_var.get(),
    )


__all__ = [
    "CorrelationContext",
    "bind_context",
    "clear_context",
    "current_context",
    "new_request_id",
    "principal_var",
    "request_id_var",
    "tenant_id_var",
    "trace_id_var",
]
