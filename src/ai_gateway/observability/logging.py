"""Structured logging configuration.

Logs are emitted as JSON in every deployed environment so that they can be indexed without
regex parsing. Correlation identifiers are injected automatically, and a scrubbing
processor removes credential-shaped values before anything is written.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any, Final

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from ai_gateway.observability.correlation import current_context

_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "x-api-key",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
        "set-cookie",
        "cookie",
    }
)
_SENSITIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9\-._~+/]{16,}=*|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
)
_MASK: Final = "[redacted]"


def _scrub(value: Any) -> Any:
    """Recursively mask credential-shaped values.

    Args:
        value: Any log value.

    Returns:
        The value with secrets masked.
    """
    if isinstance(value, str):
        return _SENSITIVE_PATTERN.sub(_MASK, value)
    if isinstance(value, dict):
        return {
            key: _MASK if str(key).lower() in _SENSITIVE_KEYS else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item) for item in value)
    return value


def scrub_secrets(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    """Structlog processor that masks sensitive values.

    Args:
        _logger: Unused wrapped logger.
        _name: Unused method name.
        event_dict: The event being logged.

    Returns:
        The scrubbed event.
    """
    for key in list(event_dict):
        if str(key).lower() in _SENSITIVE_KEYS:
            event_dict[key] = _MASK
        else:
            event_dict[key] = _scrub(event_dict[key])
    return event_dict


def add_correlation(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    """Structlog processor that injects correlation identifiers.

    Args:
        _logger: Unused wrapped logger.
        _name: Unused method name.
        event_dict: The event being logged.

    Returns:
        The enriched event.
    """
    for key, value in current_context().as_dict().items():
        event_dict.setdefault(key, value)
    return event_dict


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = True,
    service_name: str = "ai-integration-gateway",
    version: str = "1.0.0",
    environment: str = "local",
) -> None:
    """Configure structlog and the standard library logging bridge.

    Args:
        level: Minimum level to emit.
        json_output: Emit JSON when ``True``, human readable output otherwise.
        service_name: Value of the ``service`` field on every record.
        version: Value of the ``version`` field on every record.
        environment: Value of the ``environment`` field on every record.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        add_correlation,
        scrub_secrets,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    for noisy in ("uvicorn.access", "uvicorn.error", "aiokafka", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))

    structlog.contextvars.bind_contextvars(
        service=service_name, version=version, environment=environment
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structured logger.

    Args:
        name: Logger name; the calling module when omitted.

    Returns:
        The bound logger.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


__all__ = ["add_correlation", "configure_logging", "get_logger", "scrub_secrets"]
