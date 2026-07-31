"""OpenTelemetry tracing helpers.

Tracing is opt-in. When disabled, :func:`span` becomes a no-op context manager so that
call sites never need to branch.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

_provider: TracerProvider | None = None
_tracer_name = "ai_gateway"


def configure_tracing(
    *,
    service_name: str,
    environment: str,
    version: str,
    enabled: bool,
    otlp_endpoint: str | None = None,
    sample_ratio: float = 0.1,
    headers: Mapping[str, str] | None = None,
) -> None:
    """Configure the global OpenTelemetry tracer provider.

    Args:
        service_name: Service name reported in the resource.
        environment: Deployment environment.
        version: Service version.
        enabled: Whether tracing is active.
        otlp_endpoint: OTLP HTTP endpoint; falls back to a console exporter.
        sample_ratio: Probability that a root span is sampled.
        headers: Optional OTLP headers.
    """
    global _provider  # noqa: PLW0603 - process-wide singleton is intentional
    if not enabled:
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": version,
            "deployment.environment": environment,
        }
    )
    provider = TracerProvider(resource=resource, sampler=ParentBasedTraceIdRatio(sample_ratio))

    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter: Any = OTLPSpanExporter(endpoint=otlp_endpoint, headers=dict(headers or {}))
    else:
        exporter = ConsoleSpanExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider


def shutdown_tracing() -> None:
    """Flush and shut down the tracer provider."""
    global _provider  # noqa: PLW0603
    if _provider is not None:
        _provider.shutdown()
        _provider = None


@contextmanager
def span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
) -> Iterator[trace.Span]:
    """Create a span around a unit of work.

    Args:
        name: Span name.
        attributes: Span attributes.
        kind: Span kind.

    Yields:
        The active span. When tracing is disabled a non-recording span is yielded.
    """
    tracer = trace.get_tracer(_tracer_name)
    with tracer.start_as_current_span(name, kind=kind, attributes=dict(attributes or {})) as active:
        yield active


__all__ = ["configure_tracing", "shutdown_tracing", "span"]
