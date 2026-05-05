from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry, read_file_tool
from agentos.context import CapabilityPlane, ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.observability import (
    CapturePolicy,
    InMemoryTracer,
    ObservabilityConfig,
    use_observability_context,
)
from agentos.observability.instrument import instrument_query_loop
from agentos.observability.instrumented import InstrumentedQueryLoop
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import ProviderRequestBuilder, QueryLoop, SessionState


class NoOpCompressionRuntime:
    """测试用 compression runtime。"""

    def __init__(self) -> None:
        self.calls = 0

    def maybe_compress(self) -> None:
        self.calls += 1
        return None


def _build_loop(tmp_path: Path) -> tuple[QueryLoop, FakeProvider, NoOpCompressionRuntime]:
    (tmp_path / "pyproject.toml").write_text('name = "agent-os"', encoding="utf-8")
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_1",
                        name="read_file",
                        arguments={"path": "pyproject.toml"},
                    ),
                ],
                stop_reason="tool_calls",
                model="fake-model",
                provider_name="fake",
            ),
            ProviderResponse(
                content="项目名是 agent-os。",
                stop_reason="stop",
                model="fake-model",
                provider_name="fake",
            ),
        ],
    )
    context = ContextRuntime()
    messages = MessageRuntime()
    registry = ToolRegistry()
    registry.register(read_file_tool(tmp_path))
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    compression = NoOpCompressionRuntime()
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(
                capability_plane=CapabilityPlane(
                    tool_groups=[registry.capability_tool_group("Registered tools")],
                ),
            ),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        compression_runtime=compression,  # type: ignore[arg-type]
        tool_call_router=router,
        session_state=SessionState(id="s1"),
    )
    return loop, provider, compression


def test_instrument_query_loop_records_full_turn_span_tree(tmp_path: Path) -> None:
    loop, provider, compression = _build_loop(tmp_path)
    tracer = InMemoryTracer()

    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )
    answer = instrumented.run_turn("读取项目名")

    assert isinstance(instrumented, InstrumentedQueryLoop)
    assert answer == "项目名是 agent-os。"
    assert compression.calls == 2
    assert len(provider.requests) == 2
    assert [record.name for record in tracer.records] == [
        "agent.turn",
        "compression.maybe_compress",
        "provider.request.build",
        "provider.stream",
        "tool.read_file",
        "compression.maybe_compress",
        "provider.request.build",
        "provider.stream",
    ]
    root = tracer.records[0]
    assert root.parent_span_id is None
    assert root.attributes["langfuse.observation.type"] == "agent"
    assert root.attributes["langfuse.trace.name"] == "agentos.turn"
    assert root.attributes["agentos.session.id"] == "s1"
    assert root.attributes["agentos.capture.mode"] == "metadata"
    assert all(
        record.parent_span_id == root.span_id
        for record in tracer.records[1:]
    )
    request_span = tracer.records[2]
    assert request_span.attributes["langfuse.observation.type"] == "span"
    assert request_span.attributes["agentos.provider_request.messages.count"] == 1
    assert request_span.attributes["agentos.provider_request.tools.count"] >= 1
    generation_span = tracer.records[3]
    assert generation_span.attributes["langfuse.observation.type"] == "generation"
    assert generation_span.attributes["agentos.provider.tool_call_count"] == 1


def test_instrument_query_loop_metadata_mode_records_trace_input_output_summaries(tmp_path: Path) -> None:
    loop, _, _ = _build_loop(tmp_path)
    tracer = InMemoryTracer()

    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )
    instrumented.run_turn("读取项目名")

    root = tracer.records[0]
    assert "user_message_chars" in str(root.attributes["langfuse.trace.input"])
    assert "sha256" not in str(root.attributes["langfuse.trace.input"])
    assert "content_chars" in str(root.attributes["langfuse.trace.output"])
    assert "sha256" not in str(root.attributes["langfuse.trace.output"])
    assert "user_message_chars" in str(root.attributes["langfuse.observation.input"])
    assert "content_chars" in str(root.attributes["langfuse.observation.output"])
    assert "读取项目名" not in str(root.attributes["langfuse.trace.input"])
    assert "项目名是 agent-os。" not in str(root.attributes["langfuse.trace.output"])


def test_instrument_query_loop_full_mode_records_trace_input_output_content(tmp_path: Path) -> None:
    loop, _, _ = _build_loop(tmp_path)
    tracer = InMemoryTracer()

    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.full_for_local_development(),
        ),
    )
    instrumented.run_turn("读取项目名")

    root = tracer.records[0]
    assert "读取项目名" in str(root.attributes["langfuse.trace.input"])
    assert "项目名是 agent-os。" in str(root.attributes["langfuse.trace.output"])


def test_instrument_query_loop_does_not_mutate_original_loop(tmp_path: Path) -> None:
    loop, _, _ = _build_loop(tmp_path)
    original_provider = loop.provider
    original_builder = loop.request_builder
    original_router = loop.tool_call_router
    original_compression = loop.compression_runtime

    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=InMemoryTracer(),
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )

    assert instrumented is not loop
    assert loop.provider is original_provider
    assert loop.request_builder is original_builder
    assert loop.tool_call_router is original_router
    assert loop.compression_runtime is original_compression


def test_query_loop_records_trace_session_turn_and_user_metadata_on_all_spans(tmp_path: Path) -> None:
    loop, _, _ = _build_loop(tmp_path)
    tracer = InMemoryTracer()
    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )

    with use_observability_context(user_id="u_1"):
        instrumented.run_turn("读取项目名")

    root_trace_id = tracer.records[0].attributes["agentos.trace.id"]
    for record in tracer.records:
        assert record.attributes["agentos.trace.id"] == root_trace_id
        assert record.attributes["agentos.session.id"] == "s1"
        assert record.attributes["agentos.turn.id"] == "turn_1"
        assert record.attributes["langfuse.session.id"] == "s1"
        assert record.attributes["session.id"] == "s1"
        assert record.attributes["langfuse.user.id"] == "u_1"
        assert record.attributes["user.id"] == "u_1"
        assert "agentos.user.id" not in record.attributes
        assert "agentos.span.id" not in record.attributes
        assert record.attributes["langfuse.trace.metadata.turn_id"] == "turn_1"
        assert record.attributes["langfuse.trace.metadata.capture_mode"] == "metadata"


def test_query_loop_inherits_incoming_traceparent(tmp_path: Path) -> None:
    loop, _, _ = _build_loop(tmp_path)
    tracer = InMemoryTracer()
    incoming_trace_id = "1" * 32
    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )

    with use_observability_context(
        incoming_headers={
            "traceparent": f"00-{incoming_trace_id}-{'2' * 16}-01",
        },
    ):
        instrumented.run_turn("读取项目名")

    assert tracer.records[0].trace_id == incoming_trace_id
    assert tracer.records[0].attributes["agentos.trace.id"] == incoming_trace_id
    assert all(record.trace_id == incoming_trace_id for record in tracer.records)
