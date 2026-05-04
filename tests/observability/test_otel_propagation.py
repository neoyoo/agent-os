import pytest

pytest.importorskip("opentelemetry")

from agentos.observability.otel import _OTelTracer


def _test_otel_tracer() -> _OTelTracer:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    return _OTelTracer(provider.get_tracer("agentos-test"), provider)


def test_otel_tracer_injects_traceparent() -> None:
    tracer = _test_otel_tracer()
    headers: dict[str, str] = {}

    try:
        with tracer.start_span("agent.turn"):
            ids = tracer.current_trace_ids()
            tracer.inject_headers(headers)
    finally:
        tracer.shutdown()

    assert ids.trace_id is not None
    assert len(ids.trace_id) == 32
    assert "traceparent" in headers
    assert ids.trace_id in headers["traceparent"]


def test_otel_tracer_extracts_incoming_traceparent() -> None:
    trace_id = "1" * 32
    parent_span_id = "2" * 16
    tracer = _test_otel_tracer()

    try:
        with tracer.use_incoming_headers(
            {"traceparent": f"00-{trace_id}-{parent_span_id}-01"},
        ):
            with tracer.start_span("agent.turn"):
                ids = tracer.current_trace_ids()
    finally:
        tracer.shutdown()

    assert ids.trace_id == trace_id
