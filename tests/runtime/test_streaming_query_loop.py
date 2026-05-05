from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import (
    AssistantCompleted,
    AssistantContentDelta,
    ProviderRequestBuilder,
    QueryLoop,
    RunOptions,
    TurnStreamCompleted,
    TurnStreamStarted,
)


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
