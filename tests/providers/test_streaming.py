from agentos.providers import (
    FakeProvider,
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    complete_response_to_stream_events,
)


def test_complete_response_to_stream_events_emits_delta_and_completed() -> None:
    response = ProviderResponse(content="hello", stop_reason="stop")

    events = list(
        complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=ProviderStreamOptions(),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[1] == ProviderContentDelta(
        request_id="provider_1",
        index=1,
        text="hello",
    )
    assert isinstance(events[2], ProviderStreamCompleted)
    assert events[2].response is response


def test_complete_response_to_stream_events_hides_thinking_by_default() -> None:
    response = ProviderResponse(
        content="answer",
        thinking_content="private reasoning",
        stop_reason="stop",
    )

    events = list(
        complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=ProviderStreamOptions(thinking=True, show_thinking=False),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert isinstance(events[-1], ProviderStreamCompleted)
    assert events[-1].response.thinking_content == "private reasoning"


def test_complete_response_to_stream_events_can_show_thinking() -> None:
    response = ProviderResponse(
        content="answer",
        thinking_content="private reasoning",
        stop_reason="stop",
    )

    events = list(
        complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=ProviderStreamOptions(thinking=True, show_thinking=True),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderThinkingDelta",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[1].text == "private reasoning"


def test_fake_provider_streams_configured_response() -> None:
    provider = FakeProvider([ProviderResponse(content="ok", stop_reason="stop")])

    events = list(
        provider.stream(
            ProviderRequest(system="system", messages=[], tools=[]),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[1].text == "ok"
    assert events[-1].response.content == "ok"
