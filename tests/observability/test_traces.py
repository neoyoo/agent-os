from agentos.observability.events import EventLog
from agentos.observability.langfuse import LangfuseAdapter
from agentos.observability.otel import OTelAdapter
from agentos.observability.traces import EventTraceProjector
from agentos.runtime import (
    AssistantMessageAppendedEvent,
    CompressionCompletedEvent,
    EventBus,
    ProviderRequestBuiltEvent,
    RecallContextInjectedEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    TurnStartedEvent,
)


class FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}
        self.ended = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended = True


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_span(self, name: str) -> FakeSpan:
        span = FakeSpan(name)
        self.spans.append(span)
        return span


class FakeLangfuseClient:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def trace(self, **kwargs: object) -> None:
        self.events.append(dict(kwargs))


def test_event_trace_projector_records_tool_trace_without_message_content() -> None:
    log = EventLog()
    bus = EventBus(subscribers=[log])
    bus.emit(
        ToolExecutionStartedEvent(
            session_id="s1",
            turn_id="turn_1",
            tool_name="read_file",
            tool_call_id="call_1",
        ),
    )
    bus.emit(
        ToolExecutionCompletedEvent(
            session_id="s1",
            turn_id="turn_1",
            tool_name="read_file",
            tool_call_id="call_1",
        ),
    )

    records = EventTraceProjector().project(log.records)

    assert records[0].name == "tool.read_file"
    assert records[0].trace_id == "s1"
    assert records[0].span_id == "event-1"
    assert records[0].attributes["tool.name"] == "read_file"
    assert "content" not in records[0].attributes


def test_event_trace_projector_covers_lifecycle_context_and_recall_events() -> None:
    log = EventLog()
    bus = EventBus(subscribers=[log])
    bus.emit(TurnStartedEvent(session_id="s1", turn_id="turn_1", user_input="secret"))
    bus.emit(ProviderRequestBuiltEvent(session_id="s1", turn_id="turn_1"))
    bus.emit(
        AssistantMessageAppendedEvent(
            session_id="s1",
            turn_id="turn_1",
            message_id="msg_2",
        ),
    )
    bus.emit(
        CompressionCompletedEvent(
            session_id="s1",
            turn_id="turn_1",
            segment_id="seg_1",
            source_message_ids=("msg_1",),
        ),
    )
    bus.emit(
        RecallContextInjectedEvent(
            session_id="s1",
            turn_id="turn_1",
            handle="seg_1",
            message_ids=("msg_1",),
        ),
    )

    records = EventTraceProjector().project(log.records)

    assert [record.name for record in records] == [
        "turn.started",
        "provider.request",
        "message.assistant.appended",
        "compression.completed",
        "recall.injected",
    ]
    assert all(record.trace_id == "s1" for record in records)
    assert [record.span_id for record in records] == [
        "event-1",
        "event-2",
        "event-3",
        "event-4",
        "event-5",
    ]
    assert "user_input" not in records[0].attributes
    assert records[2].attributes["message.id"] == "msg_2"
    assert records[3].attributes["compression.id"] == "seg_1"
    assert records[4].attributes["recall.handle"] == "seg_1"


def test_otel_adapter_uses_injected_tracer() -> None:
    tracer = FakeTracer()
    adapter = OTelAdapter(tracer=tracer)
    log = EventLog()
    bus = EventBus(subscribers=[log])
    bus.emit(
        ToolExecutionCompletedEvent(
            session_id="s1",
            turn_id="turn_1",
            tool_name="read_file",
            tool_call_id="call_1",
        ),
    )

    adapter.record_many(EventTraceProjector().project(log.records))

    assert tracer.spans[0].name == "tool.read_file"
    assert tracer.spans[0].attributes["trace.id"] == "s1"
    assert tracer.spans[0].attributes["span.id"] == "event-1"
    assert tracer.spans[0].attributes["tool.call_id"] == "call_1"
    assert tracer.spans[0].ended is True


def test_langfuse_adapter_uses_injected_client() -> None:
    client = FakeLangfuseClient()
    adapter = LangfuseAdapter(client=client)
    log = EventLog()
    bus = EventBus(subscribers=[log])
    bus.emit(
        ToolExecutionCompletedEvent(
            session_id="s1",
            turn_id="turn_1",
            tool_name="read_file",
            tool_call_id="call_1",
        ),
    )

    adapter.record_many(EventTraceProjector().project(log.records))

    assert client.events[0]["name"] == "tool.read_file"
    assert client.events[0]["metadata"]["trace.id"] == "s1"
    assert client.events[0]["metadata"]["span.id"] == "event-1"
    assert client.events[0]["metadata"]["tool.call_id"] == "call_1"
