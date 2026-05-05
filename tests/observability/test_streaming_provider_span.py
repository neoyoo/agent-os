from collections.abc import Iterator

from agentos.observability import CapturePolicy, InMemoryTracer
from agentos.observability.instrumented import InstrumentedProvider
from agentos.providers import (
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderStreamStarted,
)


class StreamingProviderStub:
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(content="unused")

    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        yield ProviderStreamStarted(request_id="stream_1")
        yield ProviderContentDelta(request_id="stream_1", index=1, text="hel")
        yield ProviderContentDelta(request_id="stream_1", index=2, text="lo")
        yield ProviderStreamCompleted(
            request_id="stream_1",
            response=ProviderResponse(
                content="hello",
                stop_reason="stop",
                model="model-test",
                provider_name="provider-test",
            ),
            stop_reason="stop",
        )


def test_instrumented_provider_stream_finishes_span_after_completed_event() -> None:
    tracer = InMemoryTracer()
    provider = InstrumentedProvider(
        StreamingProviderStub(),
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    )

    events = list(
        provider.stream(
            ProviderRequest(system="system", messages=[], tools=[]),
            ProviderStreamOptions(),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    record = tracer.records[0]
    assert record.name == "provider.stream"
    assert record.attributes["gen_ai.request.stream"] is True
    assert record.attributes["agentos.stream.content.delta_count"] == 2
    assert record.attributes["agentos.stream.content.char_count"] == 5
    assert record.attributes["gen_ai.response.finish_reasons"] == ["stop"]


def test_instrumented_provider_stream_does_not_capture_delta_text_by_default() -> None:
    tracer = InMemoryTracer()
    provider = InstrumentedProvider(
        StreamingProviderStub(),
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    )

    list(provider.stream(ProviderRequest(system="system", messages=[], tools=[])))

    assert all(
        "text" not in event.attributes
        for event in tracer.records[0].events
        if event.name == "agentos.stream.content_delta"
    )
