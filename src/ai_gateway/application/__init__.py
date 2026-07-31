"""Application layer.

Orchestrates domain objects to satisfy use cases. It declares the abstract ports it needs
(``application.ports``) and never imports a concrete adapter, a web framework or a driver.
"""

from __future__ import annotations
