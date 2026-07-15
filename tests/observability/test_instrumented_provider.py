import asyncio
from collections.abc import AsyncIterator
from threading import Event as ThreadEvent

import pytest

from agentos import Agent
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.observability import CapturePolicy, InMemoryTracer
from agentos.observability.instrumented import InstrumentedProvider
from agentos.providers import (
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderUsage,
    ProviderInputItem,
)
from agentos.runtime import ProviderRequestBuilder, QueryLoop
from agentos.runtime.errors import AgentBusyError
from tests._context_protocol_fixtures import default_context_renderer


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
        messages=[ProviderInputItem.business_user("hello")],
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
    input_attribute = str(span.attributes["langfuse.observation.input"])
    output_attribute = str(span.attributes["langfuse.observation.output"])
    assert "system_chars" in input_attribute
    assert "message_count" in input_attribute
    assert "tool_count" in input_attribute
    assert "sha256" not in input_attribute
    assert "system text" not in input_attribute
    assert "content_chars" in output_attribute
    assert "tool_call_count" in output_attribute
    assert "sha256" not in output_attribute
    assert "done" not in output_attribute
    assert "agentos.provider_request.system.sha256" in span.attributes


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
            messages=[ProviderInputItem.business_user("hello")],
            tools=[],
        ),
    )

    span = tracer.records[0]
    input_attribute = str(span.attributes["langfuse.observation.input"])
    assert input_attribute.startswith('{"system"')
    assert input_attribute.index('"system"') < input_attribute.index('"messages"')
    assert input_attribute.index('"messages"') < input_attribute.index('"tools"')
    assert "system text" in input_attribute
    assert "done" in str(span.attributes["langfuse.observation.output"])


def test_instrumented_sync_inner_converges_with_bound_run_tracker() -> None:
    from agentos._sync_work import SyncWorkTracker, bind_sync_work_tracker

    class BlockingProvider:
        def __init__(self) -> None:
            self.started = ThreadEvent()
            self.release = ThreadEvent()
            self.finished = ThreadEvent()

        def complete(self, _request: ProviderRequest) -> ProviderResponse:
            self.started.set()
            self.release.wait()
            self.finished.set()
            return ProviderResponse(content="done")

    async def scenario() -> None:
        inner = BlockingProvider()
        provider = InstrumentedProvider(
            inner,
            tracer=InMemoryTracer(),
            capture_policy=CapturePolicy.metadata_only(),
        )
        tracker = SyncWorkTracker()

        async def events() -> AsyncIterator[object]:
            yield await provider.async_complete(
                ProviderRequest(system="system", messages=[]),
            )

        source = bind_sync_work_tracker(events(), tracker)
        consumer = asyncio.create_task(anext(source))
        try:
            assert await asyncio.to_thread(inner.started.wait, 5)
            consumer.cancel("instrumented provider cancellation")
            with pytest.raises(asyncio.CancelledError):
                await consumer

            waiter = asyncio.create_task(tracker.wait_until_idle())
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter), 0.05)
        finally:
            inner.release.set()
            await asyncio.gather(consumer, return_exceptions=True)
            await source.aclose()

        await waiter
        assert inner.finished.is_set()
        assert tracker.exceptions == ()

    asyncio.run(scenario())


def test_agent_close_waits_for_instrumented_sync_stream_worker() -> None:
    class BlockingSyncStreamProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.blocked = ThreadEvent()
            self.release = ThreadEvent()
            self.worker_finished = ThreadEvent()
            self.iterator_closed = ThreadEvent()

        def stream(
            self,
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ):
            self.calls += 1
            request_id = f"provider_{self.calls}"
            content = "blocked" if self.calls == 1 else "replacement"
            response = ProviderResponse(content=content)
            yield ProviderStreamStarted(request_id=request_id)
            yield ProviderContentDelta(request_id=request_id, index=1, text=content)
            if self.calls == 1:
                try:
                    self.blocked.set()
                    self.release.wait()
                    self.worker_finished.set()
                    yield ProviderStreamCompleted(
                        request_id=request_id,
                        response=response,
                    )
                finally:
                    self.iterator_closed.set()
                return
            yield ProviderStreamCompleted(request_id=request_id, response=response)

    async def scenario() -> None:
        inner = BlockingSyncStreamProvider()
        messages = MessageRuntime()
        provider = InstrumentedProvider(
            inner,
            tracer=InMemoryTracer(),
            capture_policy=CapturePolicy.metadata_only(),
        )
        agent = Agent(
            QueryLoop(
                context_runtime=ContextRuntime(),
                message_runtime=messages,
                request_builder=ProviderRequestBuilder(
                    context_renderer=default_context_renderer(),
                    message_runtime=messages,
                    tools=[],
                ),
                provider=provider,
            ),
        )
        stream = await agent.run("first", stream=True)
        consumer = asyncio.create_task(_consume_stream(stream))
        close_task: asyncio.Task[None] | None = None
        try:
            assert await asyncio.to_thread(inner.blocked.wait, 5)
            close_task = asyncio.create_task(stream.aclose())

            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(close_task), 0.05)
            with pytest.raises(AgentBusyError):
                await agent.run("replacement")
        finally:
            inner.release.set()
            if close_task is not None:
                await asyncio.gather(close_task, return_exceptions=True)
            await asyncio.gather(consumer, return_exceptions=True)

        assert close_task is not None
        assert close_task.exception() is None
        assert consumer.cancelled()
        assert inner.worker_finished.is_set()
        assert inner.iterator_closed.is_set()

        replacement = await agent.run("replacement")
        assert replacement.content == "replacement"

    async def _consume_stream(stream: AsyncIterator[object]) -> None:
        async for _event in stream:
            pass

    asyncio.run(scenario())
