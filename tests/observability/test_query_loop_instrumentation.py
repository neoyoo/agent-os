import asyncio
from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry, read_file_tool
from agentos.capabilities.skills import (
    FileSystemSkillSource,
    SkillRegistry,
    register_skill_loader_tools,
)
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.observability import (
    CapturePolicy,
    InMemoryTracer,
    ObservabilityConfig,
    use_observability_context,
)
from agentos.observability.instrument import instrument_query_loop
from agentos.observability.instrumented import InstrumentedQueryLoop
from agentos.providers import (
    FakeProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from agentos.runtime import (
    AssistantContentDelta,
    AsyncQueryLoop,
    ProviderRequestBuilder,
    QueryLoop,
    SessionState,
    TurnStreamCompleted,
)
from tests._context_protocol_fixtures import default_context_renderer


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
            context_renderer=default_context_renderer(),
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
    assert request_span.attributes["agentos.provider_request.messages.count"] == 2
    assert request_span.attributes["agentos.provider_request.tools.count"] >= 1
    generation_span = tracer.records[3]
    assert generation_span.attributes["langfuse.observation.type"] == "generation"
    assert generation_span.attributes["agentos.provider.tool_call_count"] == 1
    for request in provider.requests:
        tool_names = [tool["function"]["name"] for tool in request.tools]
        assert "read_file" in tool_names
        assert "Registered tools" not in request.system
        assert "read_file" not in request.system


def test_instrument_query_loop_records_streaming_turn_span_tree(
    tmp_path: Path,
) -> None:
    loop, provider, compression = _build_loop(tmp_path)
    tracer = InMemoryTracer()
    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )

    events = list(instrumented.run_turn_stream("读取项目名"))

    assert any(
        isinstance(event, AssistantContentDelta) and event.text == "项目名是 agent-os。"
        for event in events
    )
    assert isinstance(events[-1], TurnStreamCompleted)
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
    assert root.attributes["langfuse.observation.type"] == "agent"
    assert root.attributes["langfuse.trace.name"] == "agentos.turn"
    assert root.attributes["agentos.session.id"] == "s1"
    assert root.attributes["agentos.capture.mode"] == "metadata"
    assert root.attributes["agentos.final_response.length"] == len(
        "项目名是 agent-os。",
    )
    assert all(record.parent_span_id == root.span_id for record in tracer.records[1:])


def test_instrument_query_loop_records_native_async_skill_stream(
    tmp_path: Path,
) -> None:
    write_skill = tmp_path / "review.md"
    write_skill.write_text(
        (
            "---\n"
            "name: code-review\n"
            "description: Review code.\n"
            "when_to_use: Review code changes.\n"
            "---\n"
            "# Review\nFind bugs first.\n"
        ),
        encoding="utf-8",
    )

    async def collect() -> tuple[list[object], list[str], list[ProviderRequest]]:
        skill_registry = await SkillRegistry.aload(FileSystemSkillSource([tmp_path]))
        tool_registry = ToolRegistry()
        register_skill_loader_tools(tool_registry, skill_registry)
        router = ToolCallRouter(tool_registry=tool_registry)
        messages = MessageRuntime()
        provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=[
                        ProviderToolCall(
                            id="call_skill",
                            name="load_skill",
                            arguments={"skill_name": "code-review"},
                        ),
                    ],
                ),
                ProviderResponse(content="reviewed"),
            ],
        )
        loop = AsyncQueryLoop(
            context_runtime=ContextRuntime(),
            message_runtime=messages,
            request_builder=ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=router.tool_specs(),
            ),
            provider=provider,
            tool_call_router=router,
            session_state=SessionState(id="s1"),
        )
        tracer = InMemoryTracer()
        instrumented = instrument_query_loop(
            loop,
            ObservabilityConfig(
                tracer=tracer,
                capture_policy=CapturePolicy.metadata_only(),
            ),
        )

        events = [event async for event in instrumented.run_turn_stream("review")]
        return events, [record.name for record in tracer.records], provider.requests

    events, record_names, provider_requests = asyncio.run(collect())

    assert isinstance(events[-1], TurnStreamCompleted)
    assert events[-1].content == "reviewed"
    assert record_names == [
        "agent.turn",
        "provider.request.build",
        "provider.stream",
        "tool.load_skill",
        "provider.request.build",
        "provider.stream",
    ]
    for request in provider_requests:
        tool_names = [tool["function"]["name"] for tool in request.tools]
        assert "load_skill" in tool_names
        assert "code-review" not in request.system


def test_instrumented_query_loop_keeps_attachment_runtime_accessible(
    tmp_path: Path,
) -> None:
    loop, _, _ = _build_loop(tmp_path)
    attachment_runtime = object()
    loop.request_builder.attachment_runtime = attachment_runtime  # type: ignore[attr-defined]

    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=InMemoryTracer(),
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )

    assert instrumented.request_builder.attachment_runtime is attachment_runtime


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
    trace_input = str(root.attributes["langfuse.trace.input"])
    assert "读取项目名" in trace_input
    assert "latest_provider_request" in trace_input
    assert "system" in trace_input
    assert "messages" in trace_input
    assert "tools" in trace_input
    assert "项目名是 agent-os。" in str(root.attributes["langfuse.trace.output"])


def test_instrument_query_loop_stream_full_mode_records_latest_provider_request_on_root_trace(
    tmp_path: Path,
) -> None:
    loop, _, _ = _build_loop(tmp_path)
    tracer = InMemoryTracer()

    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.full_for_local_development(),
        ),
    )
    list(instrumented.run_turn_stream("读取项目名"))

    trace_input = str(tracer.records[0].attributes["langfuse.trace.input"])
    assert "latest_provider_request" in trace_input
    assert "system" in trace_input
    assert "messages" in trace_input
    assert "tools" in trace_input
    assert "读取项目名" in trace_input


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
