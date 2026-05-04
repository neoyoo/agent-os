"""通过纯 OpenTelemetry 向本地 Langfuse 写入一条最小 trace。"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV_PATH = REPO_ROOT / ".env"
DEFAULT_LANGFUSE_BASE_URL = "http://localhost:3000"
SERVICE_NAME = "agent-os-langfuse-otel-smoke"


AttributeValue = str | bool | int | float | list[str]


def _load_dotenv(path: Path = DEFAULT_DOTENV_PATH) -> bool:
    if not path.exists():
        return False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if key and not os.environ.get(key):
            os.environ[key] = value

    return True


def _configure_langfuse_base_url() -> str:
    base_url = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
    if not base_url:
        base_url = DEFAULT_LANGFUSE_BASE_URL
    return base_url.rstrip("/")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()

    print(f"Missing required environment variable: {name}", file=sys.stderr)
    print("Set it in .env or export it in your shell.", file=sys.stderr)
    sys.exit(2)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _langfuse_otel_trace_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/public/otel/v1/traces"


def _langfuse_otel_headers(public_key: str, secret_key: str) -> dict[str, str]:
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {auth}",
        "x-langfuse-ingestion-version": "4",
    }


def _langfuse_trace_attributes(
    *,
    trace_name: str,
    session_id: str,
    user_id: str,
    input_data: dict[str, Any],
) -> dict[str, AttributeValue]:
    return {
        "langfuse.trace.name": trace_name,
        "langfuse.session.id": session_id,
        "langfuse.user.id": user_id,
        "langfuse.trace.tags": ["local", "otel", "smoke-test"],
        "langfuse.trace.metadata.script": "scripts/langfuse_otel_smoke_test.py",
        "langfuse.trace.input": _json(input_data),
    }


def _generation_attributes(
    *,
    model: str,
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    usage_details: dict[str, int],
) -> dict[str, AttributeValue]:
    return {
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": model,
        "langfuse.observation.model.parameters": _json({"temperature": 0.0}),
        "langfuse.observation.input": _json(input_data),
        "langfuse.observation.output": _json(output_data),
        "langfuse.observation.usage_details": _json(usage_details),
        "gen_ai.system": "demo",
        "gen_ai.request.model": model,
    }


def _set_attributes(span, attributes: dict[str, AttributeValue]) -> None:
    for key, value in attributes.items():
        span.set_attribute(key, value)


def main() -> int:
    dotenv_loaded = _load_dotenv()
    base_url = _configure_langfuse_base_url()
    public_key = _require_env("LANGFUSE_PUBLIC_KEY")
    secret_key = _require_env("LANGFUSE_SECRET_KEY")

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    except ImportError:
        print(
            "Missing OpenTelemetry packages.\n"
            "Run with: uv run --with opentelemetry-sdk "
            "--with opentelemetry-exporter-otlp-proto-http "
            "python scripts/langfuse_otel_smoke_test.py",
            file=sys.stderr,
        )
        return 2

    endpoint = _langfuse_otel_trace_endpoint(base_url)
    headers = _langfuse_otel_headers(public_key, secret_key)
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers)))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("agent-os.langfuse-otel-smoke")

    session_id = os.environ.get("LANGFUSE_TEST_SESSION_ID", f"otel-session-{uuid4().hex[:8]}")
    user_id = os.environ.get("LANGFUSE_TEST_USER_ID", "local-user")
    prompt = os.environ.get("LANGFUSE_TEST_PROMPT", "用一句中文解释 Langfuse 的 OpenTelemetry 接入。")
    output = "通过 OpenTelemetry，可以把标准 trace 直接发送到 Langfuse，减少业务代码对 Langfuse SDK 的依赖。"
    trace_input = {"message": prompt}
    trace_output = {"answer": output, "mode": "otel"}
    trace_attributes = _langfuse_trace_attributes(
        trace_name="otel-smoke-test",
        session_id=session_id,
        user_id=user_id,
        input_data=trace_input,
    )
    trace_id = ""

    with tracer.start_as_current_span("otel-smoke-test") as root_span:
        trace_id = f"{root_span.get_span_context().trace_id:032x}"
        _set_attributes(root_span, trace_attributes)
        root_span.set_attribute("langfuse.observation.type", "span")
        root_span.set_attribute("langfuse.observation.input", _json(trace_input))

        with tracer.start_as_current_span("fake-retrieval") as retrieval_span:
            _set_attributes(retrieval_span, trace_attributes)
            retrieval_span.set_attribute("langfuse.observation.type", "span")
            retrieval_span.set_attribute("langfuse.observation.input", _json({"query": "OpenTelemetry"}))
            retrieval_span.set_attribute(
                "langfuse.observation.output",
                _json({"documents": ["Langfuse 可以作为 OTLP HTTP trace backend。"]}),
            )

        with tracer.start_as_current_span("otel-demo-generation") as generation_span:
            _set_attributes(generation_span, trace_attributes)
            _set_attributes(
                generation_span,
                _generation_attributes(
                    model="otel-demo-model",
                    input_data={"messages": [{"role": "user", "content": prompt}]},
                    output_data={"role": "assistant", "content": output},
                    usage_details={"input_tokens": 18, "output_tokens": 32},
                ),
            )

        root_span.set_attribute("langfuse.trace.output", _json(trace_output))
        root_span.set_attribute("langfuse.observation.output", _json(trace_output))

    provider.shutdown()

    print("Sent Langfuse OTEL smoke test trace.")
    print(f"Loaded .env: {dotenv_loaded}")
    print(f"OTLP endpoint: {endpoint}")
    print(f"Trace ID: {trace_id}")
    print(f"Session ID: {session_id}")
    print("Open Langfuse UI -> Traces and search for the trace name: otel-smoke-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
