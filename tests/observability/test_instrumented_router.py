import asyncio
from pathlib import Path

from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolConcurrencyPolicy,
    ToolInvocation,
    ToolInvocationContext,
    ToolRegistry,
    read_file_tool,
)
from agentos.observability import CapturePolicy, InMemoryTracer
from agentos.observability.instrumented import InstrumentedToolCallRouter
from agentos.policies import SecurityPolicy, SecurityPolicyError
from agentos.providers import ProviderToolCall


def _invocation(
    name: str,
    arguments: dict[str, object],
    *,
    call_id: str = "call_1",
) -> ToolInvocation:
    return ToolInvocation(
        name,
        arguments,
        ToolInvocationContext(
            "invocation_ea91f27fdf596bceb7c90d2578c5e988",
            "operation_d340f3861e0c6a7eefbaf707fdc69d3d",
            None,
            "session_1",
            "run_1",
            "turn_1",
            call_id,
            1,
        ),
    )


def test_instrumented_router_delegates_concurrency_policy_for_call() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="parallel_tool",
            description="并发安全工具。",
            parameters={"type": "object", "properties": {}},
            handler=lambda _invocation: "ok",
            side_effect_policy=SideEffectPolicy.PURE,
            concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
        ),
    )
    instrumented = InstrumentedToolCallRouter(
        ToolCallRouter(tool_registry=registry),
        tracer=InMemoryTracer(),
        capture_policy=CapturePolicy.metadata_only(),
    )

    contract = instrumented.tool_contract_for(
        _invocation("parallel_tool", {}, call_id="call_parallel"),
    )

    assert contract.concurrency_policy is ToolConcurrencyPolicy.PARALLEL_SAFE


def test_instrumented_router_delegates_call_preparation() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup.",
            parameters={"type": "object"},
            handler=lambda _invocation: "ok",
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    instrumented = InstrumentedToolCallRouter(
        ToolCallRouter(tool_registry=registry),
        tracer=InMemoryTracer(),
        capture_policy=CapturePolicy.metadata_only(),
    )
    call = ProviderToolCall("call_1", "lookup", {"query": "drawing"})

    assert instrumented.prepare_call(call) == call


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

    result = asyncio.run(
        instrumented.execute(
            _invocation("read_file", {"path": "pyproject.toml"}),
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
        asyncio.run(
            instrumented.execute(
                _invocation("read_file", {"path": "pyproject.toml"}),
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


def test_instrumented_router_metadata_mode_records_input_output_summaries(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('name = "agent-os"', encoding="utf-8")
    registry = ToolRegistry()
    registry.register(read_file_tool(tmp_path))
    router = ToolCallRouter(tool_registry=registry)
    tracer = InMemoryTracer()
    instrumented = InstrumentedToolCallRouter(
        router,
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    )

    asyncio.run(
        instrumented.execute(
            _invocation("read_file", {"path": "pyproject.toml"}),
        ),
    )

    span = tracer.records[0]
    assert "arguments_hidden" in str(span.attributes["langfuse.observation.input"])
    assert "sha256" not in str(span.attributes["langfuse.observation.input"])
    assert "content_chars" in str(span.attributes["langfuse.observation.output"])
    assert "sha256" not in str(span.attributes["langfuse.observation.output"])
    assert "agentos.tool.arguments.sha256" in span.attributes
    assert "pyproject.toml" not in str(span.attributes["langfuse.observation.input"])
    assert "agent-os" not in str(span.attributes["langfuse.observation.output"])
