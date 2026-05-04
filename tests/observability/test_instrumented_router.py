from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry, read_file_tool
from agentos.observability import CapturePolicy, InMemoryTracer
from agentos.observability.instrumented import InstrumentedToolCallRouter
from agentos.policies import SecurityPolicy, SecurityPolicyError
from agentos.providers import ProviderToolCall


def test_instrumented_router_records_tool_span(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('name = "agent-os"', encoding="utf-8")
    registry = ToolRegistry()
    registry.register(read_file_tool(tmp_path))
    router = ToolCallRouter(tool_registry=registry)
    tracer = InMemoryTracer()
    instrumented = InstrumentedToolCallRouter(
        router,
        tracer=tracer,
        capture_policy=CapturePolicy.full_for_local_development(),
    )

    result = instrumented.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "pyproject.toml"},
        ),
    )

    assert 'name = "agent-os"' in result.content
    span = tracer.records[0]
    assert span.name == "tool.read_file"
    assert span.status == "ok"
    assert span.attributes["langfuse.observation.type"] == "tool"
    assert span.attributes["gen_ai.operation.name"] == "execute_tool"
    assert span.attributes["gen_ai.tool.name"] == "read_file"
    assert span.attributes["gen_ai.tool.call.id"] == "call_1"
    assert span.attributes["agentos.tool.kind"] == "external"
    assert "langfuse.observation.input" in span.attributes
    assert "langfuse.observation.output" in span.attributes


def test_instrumented_router_records_error_and_reraises() -> None:
    registry = ToolRegistry()
    registry.register(read_file_tool("."))
    router = ToolCallRouter(
        tool_registry=registry,
        security_policy=SecurityPolicy(denied_tools={"read_file"}),
    )
    tracer = InMemoryTracer()
    instrumented = InstrumentedToolCallRouter(
        router,
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    )

    try:
        instrumented.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "pyproject.toml"},
            ),
        )
    except SecurityPolicyError:
        pass
    else:
        raise AssertionError("Expected SecurityPolicyError")

    span = tracer.records[0]
    assert span.name == "tool.read_file"
    assert span.status == "error"
    assert span.events[0].name == "exception"
    assert span.events[0].attributes["exception.type"] == "SecurityPolicyError"
