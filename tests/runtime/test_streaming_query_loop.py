import pytest

from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderContentDelta,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamFailed,
    ProviderStreamStarted,
)
from agentos.runtime import (
    AssistantCompleted,
    AssistantContentDelta,
    ProviderRequestBuilder,
    QueryLoop,
    RunOptions,
    TurnStreamCompleted,
    TurnStreamStarted,
)
from agentos.runtime.retry import RetryPolicy


def build_loop(
    provider: FakeProvider,
    messages: MessageRuntime | None = None,
) -> QueryLoop:
    message_runtime = messages or MessageRuntime()
    context = ContextRuntime()
    return QueryLoop(
        context_runtime=context,
        message_runtime=message_runtime,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=message_runtime,
            tools=[],
        ),
        provider=provider,
    )


def test_query_loop_streams_content_and_completes_turn() -> None:
    messages = MessageRuntime()
    loop = build_loop(
        FakeProvider([ProviderResponse(content="hello", stop_reason="stop")]),
        messages,
    )

    events = list(loop.run_turn_stream("hi"))

    assert [type(event).__name__ for event in events] == [
        "TurnStreamStarted",
        "AssistantContentDelta",
        "AssistantCompleted",
        "TurnStreamCompleted",
    ]
    assert isinstance(events[0], TurnStreamStarted)
    assert events[1] == AssistantContentDelta(index=1, text="hello")
    assert isinstance(events[2], AssistantCompleted)
    assert isinstance(events[-1], TurnStreamCompleted)
    assert messages.materialize_provider_messages() == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


class LiveDeltaProvider:
    def __init__(self) -> None:
        self.resumed_after_content_delta = False

    def stream(self, request, options=None):
        yield ProviderStreamStarted(request_id="live")
        yield ProviderContentDelta(request_id="live", index=1, text="hel")
        self.resumed_after_content_delta = True
        yield ProviderStreamCompleted(
            request_id="live",
            response=ProviderResponse(content="hel", stop_reason="stop"),
            stop_reason="stop",
        )


class FailsAfterDeltaProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request, options=None):
        self.calls += 1
        yield ProviderStreamStarted(request_id=f"live_{self.calls}")
        yield ProviderContentDelta(
            request_id=f"live_{self.calls}",
            index=1,
            text="partial",
        )
        yield ProviderStreamFailed(
            request_id=f"live_{self.calls}",
            error=RuntimeError("stream failed after partial output"),
        )


def test_query_loop_yields_content_delta_before_provider_stream_completes() -> None:
    provider = LiveDeltaProvider()
    loop = build_loop(provider)  # type: ignore[arg-type]

    events = loop.run_turn_stream("hi")

    assert isinstance(next(events), TurnStreamStarted)
    assert next(events) == AssistantContentDelta(index=1, text="hel")
    assert provider.resumed_after_content_delta is False

    assert list(events)[-1] == TurnStreamCompleted(content="hel")
    assert provider.resumed_after_content_delta is True


def test_query_loop_does_not_retry_after_streaming_visible_delta() -> None:
    provider = FailsAfterDeltaProvider()
    loop = build_loop(provider)  # type: ignore[arg-type]
    loop.retry_policy = RetryPolicy(max_retries=1, backoff_base=0, jitter=0)

    events = loop.run_turn_stream("hi")

    assert isinstance(next(events), TurnStreamStarted)
    assert next(events) == AssistantContentDelta(index=1, text="partial")
    with pytest.raises(RuntimeError, match="stream failed after partial output"):
        list(events)
    assert provider.calls == 1


def test_run_turn_consumes_stream_and_returns_final_content() -> None:
    loop = build_loop(FakeProvider([ProviderResponse(content="hello")]))

    assert loop.run_turn("hi") == "hello"


def test_query_loop_hides_thinking_by_default() -> None:
    loop = build_loop(
        FakeProvider(
            [
                ProviderResponse(
                    content="answer",
                    thinking_content="private reasoning",
                    stop_reason="stop",
                ),
            ],
        ),
    )

    events = list(loop.run_turn_stream("hi", RunOptions(thinking=True)))

    assert "AssistantThinkingDelta" not in [type(event).__name__ for event in events]


def test_query_loop_can_emit_thinking_when_requested() -> None:
    loop = build_loop(
        FakeProvider(
            [
                ProviderResponse(
                    content="answer",
                    thinking_content="private reasoning",
                    stop_reason="stop",
                ),
            ],
        ),
    )

    events = list(
        loop.run_turn_stream(
            "hi",
            RunOptions(thinking=True, show_thinking=True),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "TurnStreamStarted",
        "AssistantThinkingDelta",
        "AssistantContentDelta",
        "AssistantCompleted",
        "TurnStreamCompleted",
    ]
    assert events[1].text == "private reasoning"
