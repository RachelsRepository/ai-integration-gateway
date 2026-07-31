"""Module entrypoint: ``python -m ai_gateway``."""

from __future__ import annotations

import uvicorn

from ai_gateway.config.settings import get_settings


def main() -> None:
    """Run the ASGI server."""
    settings = get_settings()
    uvicorn.run(
        "ai_gateway.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.observability.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
