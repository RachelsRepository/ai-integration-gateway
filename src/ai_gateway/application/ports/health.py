"""Health probing port."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """The observed health of one dependency.

    Attributes:
        name: Component name.
        healthy: Whether the component is usable.
        latency_ms: Probe latency.
        detail: Optional diagnostic message; never contains secrets.
        critical: Whether readiness should fail when this component is unhealthy.
    """

    name: str
    healthy: bool
    latency_ms: int = 0
    detail: str | None = None
    critical: bool = True
    attributes: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class HealthProbe(Protocol):
    """Probes one dependency for readiness reporting."""

    @property
    def name(self) -> str:
        """Return the probed component's name."""
        ...

    @property
    def critical(self) -> bool:
        """Return whether readiness depends on this component."""
        ...

    async def check(self) -> ComponentHealth:
        """Execute the probe.

        Returns:
            The observed component health.
        """
        ...


__all__ = ["ComponentHealth", "HealthProbe"]
