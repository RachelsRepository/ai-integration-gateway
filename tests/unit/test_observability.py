"""Observability module tests."""

from __future__ import annotations

from unittest.mock import patch

from ai_gateway.observability.correlation import (
    bind_context,
    clear_context,
    current_context,
    new_request_id,
)
from ai_gateway.observability.logging import (
    add_correlation,
    configure_logging,
    get_logger,
    scrub_secrets,
)
from ai_gateway.observability.metrics import NullMetrics, PrometheusMetrics, render_metrics
from ai_gateway.observability.tracing import configure_tracing, shutdown_tracing, span


def test_correlation_context_round_trip() -> None:
    rid = new_request_id()
    tokens = bind_context(request_id=rid, tenant_id="t1", principal="u1", trace_id="tr1")
    ctx = current_context()
    assert ctx.request_id == rid
    assert ctx.as_dict()["tenant_id"] == "t1"
    clear_context(tokens)
    assert current_context().request_id is None
    clear_context()


def test_scrub_secrets_masks_credentials() -> None:
    event = scrub_secrets(
        None,
        "info",
        {
            "api_key": "sk-1234567890abcdef",
            "message": "Bearer abcdefghijklmnopqrst",
            "nested": {"password": "secret"},
        },
    )
    assert event["api_key"] == "[redacted]"
    assert "[redacted]" in event["message"]
    assert event["nested"]["password"] == "[redacted]"


def test_add_correlation_injects_context() -> None:
    tokens = bind_context(request_id="r1")
    event = add_correlation(None, "info", {"event": "test"})
    assert event["request_id"] == "r1"
    clear_context(tokens)


def test_configure_logging_console_and_json() -> None:
    configure_logging(level="DEBUG", json_output=False, service_name="test-svc")
    logger = get_logger("test")
    logger.info("hello", token="should-be-redacted")
    configure_logging(level="INFO", json_output=True)


def test_prometheus_metrics_and_render() -> None:
    metrics = PrometheusMetrics()
    metrics.increment(
        "gateway_requests_total",
        labels={"tenant": "t", "provider": "echo", "model": "m", "operation": "chat"},
    )
    metrics.observe(
        "gateway_request_latency_ms",
        12.5,
        labels={"tenant": "t", "provider": "echo", "model": "m", "operation": "chat"},
    )
    metrics.set_gauge("gateway_dlq_depth", 3.0)
    payload, content_type = render_metrics(metrics)
    assert b"gateway_requests_total" in payload
    assert content_type

    null_payload, _ = render_metrics(NullMetrics())
    assert null_payload == b""


def test_tracing_disabled_is_noop() -> None:
    configure_tracing(
        service_name="gw",
        environment="local",
        version="1.0.0",
        enabled=False,
    )
    with span("test-span", attributes={"k": "v"}):
        pass


def test_tracing_console_exporter() -> None:
    configure_tracing(
        service_name="gw",
        environment="local",
        version="1.0.0",
        enabled=True,
        sample_ratio=1.0,
    )
    with span("inner"):
        pass
    shutdown_tracing()


def test_tracing_otlp_exporter() -> None:
    from unittest.mock import MagicMock

    shutdown_tracing()
    mock_exporter = patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter")
    with mock_exporter as exporter_cls:
        exporter = MagicMock()
        exporter_cls.return_value = exporter
        configure_tracing(
            service_name="gw-otlp",
            environment="local",
            version="1.0.0",
            enabled=True,
            otlp_endpoint="http://otel:4318/v1/traces",
            headers={"Authorization": "Bearer x"},
        )
        with span("otlp-span"):
            pass
    shutdown_tracing()
