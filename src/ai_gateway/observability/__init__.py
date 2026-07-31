"""Observability primitives: correlation, logging, metrics and tracing.

This package is a leaf: it depends on nothing else in the codebase, so any layer may use
it without creating an architectural cycle.
"""

from __future__ import annotations

from ai_gateway.observability.correlation import (
    CorrelationContext,
    bind_context,
    clear_context,
    current_context,
    request_id_var,
    tenant_id_var,
)
from ai_gateway.observability.logging import configure_logging, get_logger
from ai_gateway.observability.metrics import (
    NullMetrics,
    PrometheusMetrics,
    render_metrics,
)
from ai_gateway.observability.tracing import configure_tracing, shutdown_tracing, span

__all__ = [
    "CorrelationContext",
    "NullMetrics",
    "PrometheusMetrics",
    "bind_context",
    "clear_context",
    "configure_logging",
    "configure_tracing",
    "current_context",
    "get_logger",
    "render_metrics",
    "request_id_var",
    "shutdown_tracing",
    "span",
    "tenant_id_var",
]
