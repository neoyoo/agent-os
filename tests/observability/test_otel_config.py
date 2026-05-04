import base64
import importlib
from pathlib import Path

import pytest

from agentos.observability.langfuse import (
    langfuse_otel_headers,
    langfuse_otel_trace_endpoint,
)
from agentos.observability.otel import create_otel_tracer


def test_langfuse_otel_trace_endpoint_uses_public_otlp_path() -> None:
    assert (
        langfuse_otel_trace_endpoint("http://localhost:3000/")
        == "http://localhost:3000/api/public/otel/v1/traces"
    )


def test_langfuse_otel_headers_use_basic_auth_and_ingestion_version() -> None:
    headers = langfuse_otel_headers("pk-lf-test", "sk-lf-test")
    expected_auth = base64.b64encode(b"pk-lf-test:sk-lf-test").decode("ascii")

    assert headers == {
        "Authorization": f"Basic {expected_auth}",
        "x-langfuse-ingestion-version": "4",
    }


def test_observability_import_does_not_require_opentelemetry() -> None:
    module = importlib.import_module("agentos.observability")

    assert hasattr(module, "CapturePolicy")
    assert hasattr(module, "create_langfuse_otel_tracer")


def test_pyproject_declares_observability_extra() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "observability = [" in pyproject
    assert "opentelemetry-api" in pyproject
    assert "opentelemetry-sdk" in pyproject
    assert "opentelemetry-exporter-otlp-proto-http" in pyproject


def test_create_otel_tracer_uses_batch_span_processor(monkeypatch) -> None:
    pytest.importorskip("opentelemetry")
    trace_exporter = pytest.importorskip(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    )
    trace_export = pytest.importorskip("opentelemetry.sdk.trace.export")

    created: dict[str, bool] = {}

    class FakeExporter:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def export(self, spans: object) -> object:
            return trace_export.SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

    original_batch_processor = trace_export.BatchSpanProcessor

    class RecordingBatchSpanProcessor(original_batch_processor):
        def __init__(self, exporter: object, *args: object, **kwargs: object) -> None:
            created["batch"] = True
            super().__init__(exporter, *args, **kwargs)

    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", FakeExporter)
    monkeypatch.setattr(trace_export, "BatchSpanProcessor", RecordingBatchSpanProcessor)

    tracer = create_otel_tracer(endpoint="http://collector.example/v1/traces")
    try:
        assert created == {"batch": True}
    finally:
        tracer.shutdown()
