import time
from threading import Event, Thread

from agentos.attachments import AttachmentRuntime, ImagePart, TextPart
from agentos import Agent
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import EventBus, ProviderRequestBuilder, TurnStartedEvent


def build_agent(provider: FakeProvider) -> Agent:
    context = ContextRuntime()
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": provider,
        },
    )


def build_agent_with_attachments(provider: FakeProvider) -> Agent:
    context = ContextRuntime()
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=[],
                attachment_runtime=attachments,
            ),
            "provider": provider,
        },
    )


class StaticNoticeProvider:
    def __init__(self, notices: tuple[str, ...]) -> None:
        self.notices = notices
        self.calls = 0

    def consume_notices(self) -> tuple[str, ...]:
        self.calls += 1
        notices = self.notices
        self.notices = ()
        return notices


class BlockingProvider:
    def __init__(self) -> None:
        self.requests = []
        self.first_started = Event()
        self.release_first = Event()

    def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            self.first_started.set()
            self.release_first.wait(timeout=1)
            return ProviderResponse(content="first")
        return ProviderResponse(content="second")


def build_agent_with_context(
    provider: FakeProvider,
    context: ContextRuntime,
    notice_provider: StaticNoticeProvider,
    event_bus: EventBus | None = None,
) -> Agent:
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": provider,
            "turn_notice_provider": notice_provider,
            "event_bus": event_bus,
        },
    )


def test_agent_run_returns_result_without_extra_objects() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    result = agent.run("hello")

    assert result.content == "ok"


def test_agent_run_accepts_uploaded_attachments() -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    agent = build_agent_with_attachments(provider)
    attachment = agent.attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    result = agent.run("分析图片", attachments=[attachment])

    assert result.content == "ok"
    assert provider.requests[0].messages[0].content == (
        TextPart("分析图片"),
        ImagePart(attachment),
    )


def test_agent_stream_accepts_per_turn_thinking_options() -> None:
    agent = build_agent(
        FakeProvider(
            [
                ProviderResponse(
                    content="answer",
                    thinking_content="think",
                ),
            ],
        ),
    )

    events = list(agent.stream("hello", thinking=True, show_thinking=True))

    assert "AssistantThinkingDelta" in [type(event).__name__ for event in events]


def test_agent_stream_sse_returns_sse_strings() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    chunks = list(agent.stream_sse("hello"))

    assert any(chunk.startswith("event: content_delta") for chunk in chunks)
    assert chunks[-1].startswith("event: done")


def test_agent_stream_jsonl_returns_json_lines() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    chunks = list(agent.stream_jsonl("hello"))

    assert any('"type":"content_delta"' in chunk for chunk in chunks)
    assert all(chunk.endswith("\n") for chunk in chunks)


def test_agent_callbacks_receive_specific_delta_events() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))
    deltas: list[str] = []

    result = agent.run_with_callbacks(
        "hello",
        on_content_delta=lambda text: deltas.append(text),
    )

    assert result.content == "ok"
    assert deltas == ["ok"]


def test_agent_callbacks_accept_uploaded_attachments() -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    agent = build_agent_with_attachments(provider)
    attachment = agent.attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    result = agent.run_with_callbacks("分析图片", attachments=[attachment])

    assert result.content == "ok"
    assert provider.requests[0].messages[0].content == (
        TextPart("分析图片"),
        ImagePart(attachment),
    )


def test_agent_rejects_unknown_query_loop_kwargs() -> None:
    try:
        Agent(query_loop_kwargs={"provider": FakeProvider([]), "bad_key": object()})
    except ValueError as error:
        assert "unknown query_loop_kwargs" in str(error)
        assert "bad_key" in str(error)
    else:
        raise AssertionError("Expected ValueError")


def test_agent_interrupt_causes_next_turn_to_fail_at_safe_point() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    agent.interrupt()

    try:
        agent.run("hello")
    except RuntimeError as error:
        assert "agent run interrupted" in str(error)
    else:
        raise AssertionError("Expected interrupted run to fail")

    assert agent.interrupted


def test_agent_clear_interrupt_allows_later_turns() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))
    agent.interrupt()
    agent.clear_interrupt()

    result = agent.run("hello")

    assert result.content == "ok"
    assert not agent.interrupted


def test_agent_continuation_injects_notice_without_user_message() -> None:
    provider = FakeProvider([ProviderResponse(content="checked")])
    context = ContextRuntime()
    notice_provider = StaticNoticeProvider(("Task task_1 completed.",))
    agent = build_agent_with_context(provider, context, notice_provider)

    result = agent.run_continuation()

    assert result.content == "checked"
    assert provider.requests[0].messages == []
    assert "# Runtime Notice" in provider.requests[0].system
    assert "Task task_1 completed." in provider.requests[0].system
    assert context.snapshot().runtime_notices == ()
    assert notice_provider.calls == 1


def test_agent_continuation_without_notices_does_not_call_provider() -> None:
    provider = FakeProvider([ProviderResponse(content="should not run")])
    context = ContextRuntime()
    notice_provider = StaticNoticeProvider(())
    event_bus = EventBus()
    agent = build_agent_with_context(provider, context, notice_provider, event_bus)

    result = agent.run_continuation()

    assert result.content == ""
    assert provider.requests == []
    assert event_bus.events == []
    assert notice_provider.calls == 1


def test_agent_continuation_turn_started_event_is_marked_continuation() -> None:
    provider = FakeProvider([ProviderResponse(content="checked")])
    context = ContextRuntime()
    notice_provider = StaticNoticeProvider(("Task task_1 completed.",))
    event_bus = EventBus()
    agent = build_agent_with_context(provider, context, notice_provider, event_bus)

    agent.run_continuation()

    started = [
        event for event in event_bus.events if isinstance(event, TurnStartedEvent)
    ]
    assert len(started) == 1
    assert started[0].user_input == ""
    assert started[0].is_continuation is True


def test_agent_continuation_clears_notice_when_stream_closes_before_request() -> None:
    provider = FakeProvider([ProviderResponse(content="user answer")])
    context = ContextRuntime()
    notice_provider = StaticNoticeProvider(("Task task_1 completed.",))
    agent = build_agent_with_context(provider, context, notice_provider)

    stream = agent.stream_continuation()
    first_event = next(stream)
    stream.close()

    assert type(first_event).__name__ == "TurnStreamStarted"
    assert context.snapshot().runtime_notices == ()

    result = agent.run("hello")

    assert result.content == "user answer"
    assert "# Runtime Notice" not in provider.requests[0].system
    assert "Task task_1 completed." not in provider.requests[0].system


def test_agent_user_turn_and_continuation_are_serialized() -> None:
    provider = BlockingProvider()
    context = ContextRuntime()
    notice_provider = StaticNoticeProvider(("Task task_1 completed.",))
    agent = build_agent_with_context(provider, context, notice_provider)
    continuation_result = []
    user_result = []

    continuation_thread = Thread(
        target=lambda: continuation_result.append(agent.run_continuation().content),
    )
    user_thread = Thread(
        target=lambda: user_result.append(agent.run("hello").content),
    )

    continuation_thread.start()
    assert provider.first_started.wait(timeout=1)
    user_thread.start()
    time.sleep(0.05)

    assert len(provider.requests) == 1

    provider.release_first.set()
    continuation_thread.join(timeout=1)
    user_thread.join(timeout=1)

    assert continuation_result == ["first"]
    assert user_result == ["second"]
    assert provider.requests[0].messages == []
    assert provider.requests[1].messages[-1] == {
        "role": "user",
        "content": "hello",
    }
