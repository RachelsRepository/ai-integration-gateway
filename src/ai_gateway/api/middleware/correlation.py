"""Correlation ID middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from ai_gateway.observability.correlation import bind_context, clear_context, new_request_id


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Binds a request identifier for the duration of the request."""

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID") -> None:
        """Initialise the middleware.

        Args:
            app: Downstream ASGI app.
            header_name: Correlation header name.
        """
        super().__init__(app)
        self._header = header_name

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Bind correlation context and echo the request identifier.

        Args:
            request: Incoming request.
            call_next: Downstream handler.

        Returns:
            The response with the correlation header set.
        """
        request_id = request.headers.get(self._header) or new_request_id()
        tokens = bind_context(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            clear_context(tokens)
        response.headers[self._header] = request_id
        return response


__all__ = ["CorrelationMiddleware"]
