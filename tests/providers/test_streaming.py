import asyncio
from collections.abc import AsyncIterator, Iterator

from agentos.providers import (
    FakeProvider,
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    complete_response_to_stream_events,
)
from agentos.providers.stream import MAX_PROVIDER_CONTENT_DELTA_BYTES
from agentos.runtime.provider_attempt import (
    ProviderAttemptRunner,
    provider_stream_events,
)
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuild,
    ProviderRequestReceipt,
)


def _collect_provider_attempt_content(
    provider: object,
) -> tuple[ProviderContentDelta, ...]:
    async def scenario() -> tuple[ProviderContentDelta, ...]:
        async def on_retry(_attempt: int, _error: Exception) -> None:
            raise AssertionError("retry is not expected")

        runner = ProviderAttemptRunner(
            request_factory=lambda: ProviderRequestBuild(
                request=ProviderRequest(system="system", messages=()),
                receipt=ProviderRequestReceipt(()),
            ),
            stream_provider=lambda request, options: provider_stream_events(
                provider,  # type: ignore[arg-type]
                request,
                options,
                request_id_factory=lambda: "unused",
            ),
            before_call=lambda request: request,
            after_call=lambda _request, response: response,
            ensure_usable=lambda _response: None,
            consume_temporary=lambda _temporary_ids: None,
            retry_policy=None,
            on_retry=on_retry,
        )
        events = [
            event
            async for event in runner.run_stream(None)
            if type(event) is ProviderContentDelta
        ]
        return tuple(events)

    return asyncio.run(scenario())


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


def test_complete_response_splits_large_content_delta_without_changing_content() -> None:
    content = "x" * (MAX_PROVIDER_CONTENT_DELTA_BYTES + 1)
    events = tuple(
        complete_response_to_stream_events(
            request_id="provider_1",
            response=ProviderResponse(content=content),
        ),
    )
    deltas = tuple(event for event in events if type(event) is ProviderContentDelta)

    assert len(deltas) == 2
    assert "".join(event.text for event in deltas) == content
    assert all(
        len(event.text.encode("utf-8")) <= MAX_PROVIDER_CONTENT_DELTA_BYTES
        for event in deltas
    )


def test_native_async_stream_splits_large_unicode_delta_preserving_identity() -> None:
    content = "中文🙂" * (MAX_PROVIDER_CONTENT_DELTA_BYTES // 10 + 1)

    class NativeAsyncProvider:
        def async_stream(
            self,
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderContentDelta | ProviderStreamCompleted]:
            async def events() -> AsyncIterator[
                ProviderContentDelta | ProviderStreamCompleted
            ]:
                yield ProviderContentDelta("provider_async", 7, content)
                yield ProviderStreamCompleted(
                    "provider_async",
                    ProviderResponse(content=content),
                )

            return events()

    deltas = _collect_provider_attempt_content(NativeAsyncProvider())

    assert len(deltas) > 1
    assert "".join(event.text for event in deltas) == content
    assert all(event.request_id == "provider_async" for event in deltas)
    assert all(event.index == 7 for event in deltas)
    assert all(
        len(event.text.encode("utf-8")) <= MAX_PROVIDER_CONTENT_DELTA_BYTES
        for event in deltas
    )


def test_sync_stream_splits_large_unicode_delta_preserving_identity() -> None:
    content = "图纸🚀" * (MAX_PROVIDER_CONTENT_DELTA_BYTES // 10 + 1)

    class SyncProvider:
        def stream(
            self,
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> Iterator[ProviderContentDelta | ProviderStreamCompleted]:
            yield ProviderContentDelta("provider_sync", 11, content)
            yield ProviderStreamCompleted(
                "provider_sync",
                ProviderResponse(content=content),
            )

    deltas = _collect_provider_attempt_content(SyncProvider())

    assert len(deltas) > 1
    assert "".join(event.text for event in deltas) == content
    assert all(event.request_id == "provider_sync" for event in deltas)
    assert all(event.index == 11 for event in deltas)
    assert all(
        len(event.text.encode("utf-8")) <= MAX_PROVIDER_CONTENT_DELTA_BYTES
        for event in deltas
    )


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
