"""Exception handlers that translate domain errors into HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ai_gateway.domain.errors import DomainError, RateLimitExceededError
from ai_gateway.observability.logging import get_logger

logger = get_logger(__name__)
_CLIENT_CLOSED_STATUS = 499
_FALLBACK_CLIENT_ERROR = 400


def install_exception_handlers(app: FastAPI) -> None:
    """Register domain and catch-all exception handlers.

    Args:
        app: FastAPI application.
    """

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitExceededError):
            headers["Retry-After"] = str(exc.retry_after_seconds)
        status = (
            _FALLBACK_CLIENT_ERROR if exc.http_status == _CLIENT_CLOSED_STATUS else exc.http_status
        )
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred",
                    "details": {},
                }
            },
        )


__all__ = ["install_exception_handlers"]
