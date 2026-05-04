from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "langfuse_otel_smoke_test.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("langfuse_otel_smoke_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builds_langfuse_otel_trace_endpoint_from_base_url():
    script = _load_script_module()

    assert (
        script._langfuse_otel_trace_endpoint("http://localhost:3000/")
        == "http://localhost:3000/api/public/otel/v1/traces"
    )


def test_builds_basic_auth_headers_without_plaintext_secret():
    script = _load_script_module()

    headers = script._langfuse_otel_headers("pk-lf-test", "sk-lf-secret")

    assert headers["x-langfuse-ingestion-version"] == "4"
    assert headers["Authorization"].startswith("Basic ")
    assert "sk-lf-secret" not in headers["Authorization"]
    encoded = headers["Authorization"].removeprefix("Basic ")
    assert base64.b64decode(encoded).decode("utf-8") == "pk-lf-test:sk-lf-secret"


def test_builds_langfuse_trace_and_generation_attributes():
    script = _load_script_module()

    trace_attributes = script._langfuse_trace_attributes(
        trace_name="otel-smoke-test",
        session_id="session-123",
        user_id="user-456",
        input_data={"message": "hello"},
    )
    generation_attributes = script._generation_attributes(
        model="demo-model",
        input_data={"messages": [{"role": "user", "content": "hello"}]},
        output_data={"role": "assistant", "content": "world"},
        usage_details={"input_tokens": 1, "output_tokens": 1},
    )

    assert trace_attributes["langfuse.trace.name"] == "otel-smoke-test"
    assert trace_attributes["langfuse.session.id"] == "session-123"
    assert trace_attributes["langfuse.user.id"] == "user-456"
    assert json.loads(trace_attributes["langfuse.trace.input"]) == {"message": "hello"}
    assert generation_attributes["langfuse.observation.type"] == "generation"
    assert generation_attributes["langfuse.observation.model.name"] == "demo-model"
    assert json.loads(generation_attributes["langfuse.observation.usage_details"]) == {
        "input_tokens": 1,
        "output_tokens": 1,
    }
