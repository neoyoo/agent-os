import asyncio

import pytest

from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderContentDelta,
    ProviderResponse,
    ProviderStreamCancelled,
    ProviderStreamCompleted,
    ProviderStreamFailed,
    ProviderStreamStarted,
)
from agentos.runtime import (
    Agent,
    AssistantCompleted,
    AssistantContentDelta,
    ContextLoaded,
    FinalResult,
    PlanUpdated,
    ProviderRequestBuilder,
    QueryLoop,
    RunOptions,
    RunRequest,
    StatusUpdate,
    TurnStreamCompleted,
    TurnStreamFailed,
    TurnStreamStarted,
    UserTurnInput,
)
from agentos.runtime.retry import RetryPolicy
from tests._context_protocol_fixtures import default_context_renderer


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
            context_renderer=default_context_renderer(),
            message_runtime=message_runtime,
            tools=[],
        ),
        provider=provider,
    )


async def collect_events(
    loop: QueryLoop,
    user_message: str,
    options: RunOptions | None = None,
) -> list[object]:
    stream = await loop.execute(
        RunRequest(UserTurnInput(user_message), options or RunOptions()),
    )
    async with stream:
        return [event async for event in stream]


def test_query_loop_streams_content_and_completes_turn() -> None:
    messages = MessageRuntime()
    loop = build_loop(
        FakeProvider([ProviderResponse(content="hello", stop_reason="stop")]),
        messages,
    )

    events = asyncio.run(collect_events(loop, "hi"))

    assert [type(event).__name__ for event in events] == [
        "TurnStreamStarted",
        "StatusUpdate",
        "PlanUpdated",
        "StatusUpdate",
        "ContextLoaded",
        "StatusUpdate",
        "AssistantContentDelta",
        "AssistantCompleted",
        "FinalResult",
        "TurnStreamCompleted",
    ]
    assert isinstance(events[0], TurnStreamStarted)
    assert isinstance(events[1], StatusUpdate)
    assert isinstance(events[2], PlanUpdated)
    assert isinstance(events[4], ContextLoaded)
    assert events[6] == AssistantContentDelta(index=1, text="hello")
    assert isinstance(events[7], AssistantCompleted)
    assert events[8] == FinalResult(content="hello")
    assert isinstance(events[-1], TurnStreamCompleted)
    assert [
        (message.role, message.content) for message in messages.materialize_active()
    ] == [("user", "hi"), ("assistant", "hello")]


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


class RecordsFirstRequestProvider:
    def __init__(self) -> None:
        self.requests = []

    def stream(self, request, options=None):
        del options
        self.requests.append(request)
        yield ProviderStreamStarted(request_id="recorded")
        yield ProviderStreamCompleted(
            request_id="recorded",
            response=ProviderResponse(content="done", stop_reason="stop"),
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


class CancelsBeforeDeltaProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request, options=None):
        self.calls += 1
        yield ProviderStreamStarted(request_id=f"cancel_{self.calls}")
        if self.calls == 1:
            yield ProviderStreamCancelled(request_id="cancel_1")
            return
        yield ProviderStreamCompleted(
            request_id="cancel_2",
            response=ProviderResponse(content="recovered", stop_reason="stop"),
            stop_reason="stop",
        )


class CancelsAfterDeltaProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request, options=None):
        self.calls += 1
        yield ProviderStreamStarted(request_id=f"cancel_{self.calls}")
        yield ProviderContentDelta(
            request_id=f"cancel_{self.calls}",
            index=1,
            text="partial",
        )
        yield ProviderStreamCancelled(request_id=f"cancel_{self.calls}")


def test_query_loop_yields_content_delta_before_provider_stream_completes() -> None:
    async def scenario() -> None:
        provider = LiveDeltaProvider()
        loop = build_loop(provider)  # type: ignore[arg-type]
        stream = await loop.execute(RunRequest(UserTurnInput("hi")))

        async with stream:
            assert isinstance(await anext(stream), TurnStreamStarted)
            assert isinstance(await anext(stream), StatusUpdate)
            assert isinstance(await anext(stream), PlanUpdated)
            assert isinstance(await anext(stream), StatusUpdate)
            assert isinstance(await anext(stream), ContextLoaded)
            assert isinstance(await anext(stream), StatusUpdate)
            assert await anext(stream) == AssistantContentDelta(index=1, text="hel")
            assert provider.resumed_after_content_delta is False

            assert [event async for event in stream][-1] == TurnStreamCompleted(
                content="hel",
            )
            assert provider.resumed_after_content_delta is True

    asyncio.run(scenario())


def test_first_provider_attempt_starts_before_first_external_yield() -> None:
    async def scenario() -> None:
        messages = MessageRuntime()
        provider = RecordsFirstRequestProvider()
        loop = build_loop(provider, messages)  # type: ignore[arg-type]
        stream = await loop.execute(RunRequest(UserTurnInput("hi")))

        async with stream:
            assert isinstance(await anext(stream), TurnStreamStarted)
            assert len(provider.requests) == 1
            messages.append_user("state added after the first external yield")
            assert [event async for event in stream][-1] == TurnStreamCompleted(
                content="done",
            )

        assert [
            item.content[0].text  # type: ignore[union-attr]
            for item in provider.requests[0].messages
            if item.kind == "business_message"
        ] == ["hi"]

    asyncio.run(scenario())


def test_query_loop_does_not_retry_after_streaming_visible_delta() -> None:
    async def scenario() -> None:
        provider = FailsAfterDeltaProvider()
        loop = build_loop(provider)  # type: ignore[arg-type]
        loop.retry_policy = RetryPolicy(max_retries=1, backoff_base=0, jitter=0)
        stream = await loop.execute(RunRequest(UserTurnInput("hi")))

        assert isinstance(await anext(stream), TurnStreamStarted)
        assert isinstance(await anext(stream), StatusUpdate)
        assert isinstance(await anext(stream), PlanUpdated)
        assert isinstance(await anext(stream), StatusUpdate)
        assert isinstance(await anext(stream), ContextLoaded)
        assert isinstance(await anext(stream), StatusUpdate)
        assert await anext(stream) == AssistantContentDelta(index=1, text="partial")
        failed = await anext(stream)
        assert isinstance(failed, TurnStreamFailed)
        with pytest.raises(RuntimeError, match="stream failed after partial output"):
            await anext(stream)
        assert provider.calls == 1

    asyncio.run(scenario())


def test_provider_stream_cancelled_before_delta_follows_retry_policy() -> None:
    async def scenario() -> None:
        provider = CancelsBeforeDeltaProvider()
        loop = build_loop(provider)  # type: ignore[arg-type]
        loop.retry_policy = RetryPolicy(max_retries=1, backoff_base=0, jitter=0)

        events = await collect_events(loop, "hi")

        assert events[-1] == TurnStreamCompleted(content="recovered")
        assert provider.calls == 2

    asyncio.run(scenario())


def test_provider_stream_cancelled_after_delta_does_not_retry() -> None:
    async def scenario() -> None:
        provider = CancelsAfterDeltaProvider()
        loop = build_loop(provider)  # type: ignore[arg-type]
        loop.retry_policy = RetryPolicy(max_retries=1, backoff_base=0, jitter=0)
        stream = await loop.execute(RunRequest(UserTurnInput("hi")))

        assert isinstance(await anext(stream), TurnStreamStarted)
        assert isinstance(await anext(stream), StatusUpdate)
        assert isinstance(await anext(stream), PlanUpdated)
        assert isinstance(await anext(stream), StatusUpdate)
        assert isinstance(await anext(stream), ContextLoaded)
        assert isinstance(await anext(stream), StatusUpdate)
        assert await anext(stream) == AssistantContentDelta(index=1, text="partial")
        failed = await anext(stream)
        assert isinstance(failed, TurnStreamFailed)
        with pytest.raises(RuntimeError, match="provider stream was cancelled"):
            await anext(stream)
        assert provider.calls == 1

    asyncio.run(scenario())


def test_agent_collector_consumes_stream_and_returns_final_content() -> None:
    loop = build_loop(FakeProvider([ProviderResponse(content="hello")]))

    outcome = asyncio.run(Agent(loop).run("hi"))

    assert outcome.content == "hello"


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

    events = asyncio.run(
        collect_events(loop, "hi", RunOptions(thinking=True)),
    )

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

    events = asyncio.run(
        collect_events(
            loop,
            "hi",
            RunOptions(thinking=True, show_thinking=True),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "TurnStreamStarted",
        "StatusUpdate",
        "PlanUpdated",
        "StatusUpdate",
        "ContextLoaded",
        "StatusUpdate",
        "AssistantThinkingDelta",
        "AssistantContentDelta",
        "AssistantCompleted",
        "FinalResult",
        "TurnStreamCompleted",
    ]
    assert events[6].text == "private reasoning"
