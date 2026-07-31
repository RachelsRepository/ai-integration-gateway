"""Metrics port.

The application records business-meaningful measurements; the adapter decides whether
they become Prometheus series, OTLP metrics or nothing at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsRecorder(Protocol):
    """Records counters, histograms and gauges."""

    def increment(
        self, name: str, *, value: float = 1.0, labels: Mapping[str, str] | None = None
    ) -> None:
        """Increment a counter.

        Args:
            name: Metric name.
            value: Increment step.
            labels: Metric labels.
        """
        ...

    def observe(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record an observation in a histogram.

        Args:
            name: Metric name.
            value: Observed value.
            labels: Metric labels.
        """
        ...

    def set_gauge(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None:
        """Set a gauge to an absolute value.

        Args:
            name: Metric name.
            value: New value.
            labels: Metric labels.
        """
        ...


__all__ = ["MetricsRecorder"]
