"""Prometheus metrics adapter.

Implements :class:`~ai_gateway.application.ports.metrics.MetricsRecorder` against the
Prometheus client library and exposes a text renderer for the ``/metrics`` scrape
endpoint.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

_LATENCY_BUCKETS: Final[tuple[float, ...]] = (
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1_000,
    2_500,
    5_000,
    10_000,
    30_000,
    60_000,
)


class NullMetrics:
    """A no-op metrics recorder used when metrics are disabled."""

    def increment(
        self, name: str, *, value: float = 1.0, labels: Mapping[str, str] | None = None
    ) -> None:
        """Discard a counter increment.

        Args:
            name: Metric name.
            value: Increment step.
            labels: Metric labels.
        """

    def observe(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        """Discard a histogram observation.

        Args:
            name: Metric name.
            value: Observed value.
            labels: Metric labels.
        """

    def set_gauge(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None:
        """Discard a gauge update.

        Args:
            name: Metric name.
            value: New value.
            labels: Metric labels.
        """


class PrometheusMetrics:
    """Metrics recorder backed by a Prometheus :class:`CollectorRegistry`."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        """Initialise the recorder.

        Args:
            registry: Collector registry; a fresh one is created when omitted.
        """
        self.registry = registry or CollectorRegistry()
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._gauges: dict[str, Gauge] = {}
        self._install_defaults()

    def _install_defaults(self) -> None:
        self._histogram(
            "gateway_request_latency_ms",
            "End-to-end request latency in milliseconds",
            ("tenant", "provider", "model", "operation"),
        )
        self._histogram(
            "gateway_provider_latency_ms",
            "Upstream provider call latency in milliseconds",
            ("provider", "model"),
        )
        self._counter(
            "gateway_requests_total",
            "Total billable gateway requests",
            ("tenant", "provider", "model", "operation"),
        )
        self._counter(
            "gateway_tokens_total",
            "Total tokens consumed",
            ("tenant", "provider", "model", "operation"),
        )
        self._counter(
            "gateway_cost_micros_total",
            "Total billable cost in micro-currency units",
            ("tenant", "provider", "model", "operation"),
        )
        self._counter(
            "gateway_provider_calls_total",
            "Upstream provider calls by outcome",
            ("provider", "operation", "outcome"),
        )
        self._counter(
            "gateway_provider_errors_total",
            "Upstream provider errors by reason",
            ("provider", "reason"),
        )
        self._counter(
            "gateway_rate_limited_total",
            "Requests rejected by rate limiting",
            ("tenant",),
        )
        self._counter(
            "gateway_quota_exceeded_total",
            "Requests rejected by quota enforcement",
            ("tenant", "dimension"),
        )
        self._counter(
            "gateway_cache_events_total",
            "Cache hits, misses and corruptions",
            ("result",),
        )
        self._gauge(
            "gateway_rate_limit_remaining",
            "Remaining rate-limit tokens for a tenant",
            ("tenant",),
        )
        self._gauge(
            "gateway_circuit_state",
            "Circuit breaker state (0=closed, 1=half_open, 2=open)",
            ("provider",),
        )
        self._gauge(
            "gateway_dlq_depth",
            "Number of records in the dead-letter queue",
            (),
        )

    def increment(
        self, name: str, *, value: float = 1.0, labels: Mapping[str, str] | None = None
    ) -> None:
        """Increment a counter.

        Args:
            name: Metric name.
            value: Increment step.
            labels: Metric labels.
        """
        counter = self._counters.get(name) or self._counter(
            name, name, tuple((labels or {}).keys())
        )
        if labels:
            counter.labels(**labels).inc(value)
        else:
            counter.inc(value)

    def observe(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        """Observe a histogram sample.

        Args:
            name: Metric name.
            value: Observed value.
            labels: Metric labels.
        """
        histogram = self._histograms.get(name) or self._histogram(
            name, name, tuple((labels or {}).keys())
        )
        if labels:
            histogram.labels(**labels).observe(value)
        else:
            histogram.observe(value)

    def set_gauge(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None:
        """Set a gauge.

        Args:
            name: Metric name.
            value: New value.
            labels: Metric labels.
        """
        gauge = self._gauges.get(name) or self._gauge(name, name, tuple((labels or {}).keys()))
        if labels:
            gauge.labels(**labels).set(value)
        else:
            gauge.set(value)

    def _counter(self, name: str, documentation: str, labelnames: tuple[str, ...]) -> Counter:
        metric = Counter(name, documentation, labelnames=labelnames, registry=self.registry)
        self._counters[name] = metric
        return metric

    def _histogram(self, name: str, documentation: str, labelnames: tuple[str, ...]) -> Histogram:
        metric = Histogram(
            name,
            documentation,
            labelnames=labelnames,
            buckets=_LATENCY_BUCKETS if name.endswith("_ms") else Histogram.DEFAULT_BUCKETS,
            registry=self.registry,
        )
        self._histograms[name] = metric
        return metric

    def _gauge(self, name: str, documentation: str, labelnames: tuple[str, ...]) -> Gauge:
        metric = Gauge(name, documentation, labelnames=labelnames, registry=self.registry)
        self._gauges[name] = metric
        return metric


def render_metrics(recorder: object) -> tuple[bytes, str]:
    """Render the Prometheus text exposition format.

    Args:
        recorder: Metrics recorder.

    Returns:
        The payload and its content type.
    """
    if isinstance(recorder, PrometheusMetrics):
        return generate_latest(recorder.registry), CONTENT_TYPE_LATEST
    return b"", CONTENT_TYPE_LATEST


__all__ = ["NullMetrics", "PrometheusMetrics", "render_metrics"]
