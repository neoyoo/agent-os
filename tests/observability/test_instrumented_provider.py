from agentos.observability import CapturePolicy, InMemoryTracer
from agentos.observability.instrumented import InstrumentedProvider
from agentos.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)


class RecordingProvider:
    def __init__(self, response: ProviderResponse) -> None:
        self.response = response
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return self.response


def test_instrumented_provider_records_generation_span_without_changing_response() -> None:
    tracer = InMemoryTracer()
    response = ProviderResponse(
        content="done",
        stop_reason="stop",
        usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        model="gpt-test",
        provider_name="openai",
        response_id="resp_1",
    )
    provider = RecordingProvider(response)
    instrumented = InstrumentedProvider(
        provider,
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    )
    request = ProviderRequest(
        system="system text",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
    )

    result = instrumented.complete(request)

    assert result is response
    assert provider.requests == [request]
    span = tracer.records[0]
    assert span.name == "provider.complete"
    assert span.status == "ok"
    assert span.attributes["langfuse.observation.type"] == "generation"
    assert span.attributes["gen_ai.operation.name"] == "chat"
    assert span.attributes["gen_ai.provider.name"] == "openai"
    assert span.attributes["gen_ai.request.model"] == "gpt-test"
    assert span.attributes["gen_ai.response.finish_reasons"] == ["stop"]
    assert span.attributes["gen_ai.usage.input_tokens"] == 10
    assert span.attributes["gen_ai.usage.output_tokens"] == 5
    assert span.attributes["gen_ai.usage.total_tokens"] == 15
    assert span.attributes["agentos.provider.response_id"] == "resp_1"
    assert span.attributes["agentos.provider.tool_call_count"] == 0
    assert "langfuse.observation.input" not in span.attributes
    assert "langfuse.observation.output" not in span.attributes


def test_instrumented_provider_full_capture_records_input_and_output() -> None:
    tracer = InMemoryTracer()
    provider = RecordingProvider(
        ProviderResponse(
            content="done",
            stop_reason="stop",
            model="gpt-test",
            provider_name="openai",
        ),
    )
    instrumented = InstrumentedProvider(
        provider,
        tracer=tracer,
        capture_policy=CapturePolicy.full_for_local_development(),
    )

    instrumented.complete(
        ProviderRequest(
            system="system text",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        ),
    )

    span = tracer.records[0]
    assert "system text" in str(span.attributes["langfuse.observation.input"])
    assert "done" in str(span.attributes["langfuse.observation.output"])
