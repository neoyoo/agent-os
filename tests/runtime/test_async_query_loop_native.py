import asyncio
from collections.abc import AsyncIterator
from threading import Event as ThreadEvent

import pytest

from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolInvocation,
    ToolRegistry,
)
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import (
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderToolCall,
)
from agentos.runtime import (
    Agent,
    AgentBusyError,
    ProviderRequestBuilder,
    QueryLoop,
    RetryPolicy,
    SessionState,
    TurnStreamCompleted,
)
from tests._context_protocol_fixtures import default_context_renderer


def _request_builder(
    messages: MessageRuntime,
    router: ToolCallRouter | None = None,
) -> ProviderRequestBuilder:
    return ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        tools=[] if router is None else router.tool_specs(),
    )


def test_async_handler_awaited_not_returned_as_coroutine() -> None:
    async def lookup(_invocation: ToolInvocation) -> str:
        await asyncio.sleep(0)
        return "async-ok"

    async def run() -> tuple[str, list[object]]:
        context = ContextRuntime()
        messages = MessageRuntime()
        registry = ToolRegistry()
        registry.register(
            RegisteredTool(
                name="lookup",
                description="Lookup.",
                parameters={"type": "object", "properties": {}},
                handler=lookup,
                side_effect_policy=SideEffectPolicy.PURE,
            ),
        )
        router = ToolCallRouter(tool_registry=registry, context_runtime=context)
        loop = QueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages, router),
            provider=_TwoStepProvider("lookup"),
            tool_call_router=router,
            session_state=SessionState(id="session_async_handler"),
        )
        outcome = await Agent(loop).run("hello")
        return outcome.content, messages.materialize_active()

    result, provider_messages = asyncio.run(run())

    assert result == "done"
    assert provider_messages[-2].role == "tool"
    assert provider_messages[-2].content == "async-ok"


def test_sync_handler_still_works_in_unified_query_loop() -> None:
    calls: list[dict[str, object]] = []

    def lookup(invocation: ToolInvocation) -> str:
        calls.append(dict(invocation.arguments))
        return "sync-ok"

    async def run() -> str:
        context = ContextRuntime()
        messages = MessageRuntime()
        registry = ToolRegistry()
        registry.register(
            RegisteredTool(
                name="lookup",
                description="Lookup.",
                parameters={"type": "object", "properties": {}},
                handler=lookup,
                side_effect_policy=SideEffectPolicy.PURE,
            ),
        )
        router = ToolCallRouter(tool_registry=registry, context_runtime=context)
        loop = QueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages, router),
            provider=_TwoStepProvider("lookup"),
            tool_call_router=router,
            session_state=SessionState(id="session_sync_handler"),
        )
        outcome = await Agent(loop).run("hello")
        return outcome.content

    result = asyncio.run(run())

    assert result == "done"
    assert calls == [{"value": "same"}]


def test_async_provider_stream_is_awaited_without_executor_bridge() -> None:
    class AsyncOnlyProvider:
        complete_called = False

        def complete(self, request: ProviderRequest) -> ProviderResponse:
            self.complete_called = True
            raise AssertionError("native async loop must not call sync complete")

        async def async_stream(
            self,
            request: ProviderRequest,
            options: ProviderStreamOptions,
        ):
            yield ProviderStreamStarted(request_id="async_request")
            yield ProviderContentDelta(
                request_id="async_request",
                index=1,
                text="async",
            )
            yield ProviderStreamCompleted(
                request_id="async_request",
                response=ProviderResponse(content="async"),
            )

    async def collect() -> tuple[list[object], bool]:
        context = ContextRuntime()
        messages = MessageRuntime()
        provider = AsyncOnlyProvider()
        loop = QueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages),
            provider=provider,  # type: ignore[arg-type]
        )
        stream = await Agent(loop).run("hello", stream=True)
        async with stream:
            events = [event async for event in stream]
        return events, provider.complete_called

    events, complete_called = asyncio.run(collect())

    assert complete_called is False
    assert events[-1] == TurnStreamCompleted(content="async")


def test_first_provider_attempt_starts_before_first_external_yield() -> None:
    class RecordingAsyncProvider:
        def __init__(self) -> None:
            self.requests: list[ProviderRequest] = []

        def complete(self, request: ProviderRequest) -> ProviderResponse:
            raise AssertionError("native async loop must not call sync complete")

        async def async_stream(
            self,
            request: ProviderRequest,
            options: ProviderStreamOptions,
        ):
            del options
            self.requests.append(request)
            yield ProviderStreamStarted(request_id="async_recorded")
            yield ProviderStreamCompleted(
                request_id="async_recorded",
                response=ProviderResponse(content="done", stop_reason="stop"),
                stop_reason="stop",
            )

    async def collect() -> tuple[list[object], RecordingAsyncProvider]:
        context = ContextRuntime()
        messages = MessageRuntime()
        provider = RecordingAsyncProvider()
        loop = QueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages),
            provider=provider,  # type: ignore[arg-type]
        )
        stream = await Agent(loop).run("hello", stream=True)
        events = [await anext(stream)]
        assert len(provider.requests) == 1
        messages.append_user("state added after the first external yield")
        events.extend([event async for event in stream])
        return events, provider

    events, provider = asyncio.run(collect())

    assert [
        item.content[0].text  # type: ignore[union-attr]
        for item in provider.requests[0].messages
        if item.kind == "business_message"
    ] == ["hello"]
    assert events[-1] == TurnStreamCompleted(content="done")


def test_close_after_first_external_event_closes_provider_and_releases_lease() -> None:
    class CloseAwareProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.closed = asyncio.Event()

        async def async_stream(
            self,
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            self.calls += 1
            if self.calls > 1:
                yield ProviderStreamStarted(request_id="replacement")
                yield ProviderStreamCompleted(
                    request_id="replacement",
                    response=ProviderResponse(content="replacement"),
                )
                return
            try:
                self.started.set()
                yield ProviderStreamStarted(request_id="first")
                await asyncio.Event().wait()
            finally:
                self.closed.set()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        close_errors: list[BaseException | None] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda _loop, context: close_errors.append(context.get("exception")),
        )
        try:
            messages = MessageRuntime()
            provider = CloseAwareProvider()
            agent = Agent(
                QueryLoop(
                    context_runtime=ContextRuntime(),
                    message_runtime=messages,
                    request_builder=_request_builder(messages),
                    provider=provider,  # type: ignore[arg-type]
                ),
            )
            stream = await agent.run("hello", stream=True)

            await anext(stream)
            assert provider.started.is_set()
            await stream.aclose()

            assert provider.closed.is_set()
            outcome = await agent.run("replacement")
            assert outcome.content == "replacement"
            await asyncio.sleep(0)
            assert not any(
                isinstance(error, RuntimeError)
                and "asynchronous generator is already running" in str(error)
                for error in close_errors
            )
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())


def test_async_provider_stream_retries_failure_before_visible_delta() -> None:
    class FlakyAsyncProvider:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request: ProviderRequest) -> ProviderResponse:
            raise AssertionError("native async loop must not call sync complete")

        async def async_stream(
            self,
            request: ProviderRequest,
            options: ProviderStreamOptions,
        ):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary connect failure")
            yield ProviderStreamStarted(request_id="async_retry")
            yield ProviderContentDelta(
                request_id="async_retry",
                index=1,
                text="recovered",
            )
            yield ProviderStreamCompleted(
                request_id="async_retry",
                response=ProviderResponse(content="recovered"),
            )

    async def collect() -> tuple[list[object], int]:
        context = ContextRuntime()
        messages = MessageRuntime()
        provider = FlakyAsyncProvider()
        loop = QueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages),
            provider=provider,  # type: ignore[arg-type]
            retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
        )
        stream = await Agent(loop).run("hello", stream=True)
        async with stream:
            events = [event async for event in stream]
        return events, provider.calls

    events, calls = asyncio.run(collect())

    assert events[-1] == TurnStreamCompleted(content="recovered")
    assert calls == 2


def test_agent_stream_uses_unified_query_loop() -> None:
    async def collect() -> list[object]:
        context = ContextRuntime()
        messages = MessageRuntime()
        agent = Agent(
            query_loop=QueryLoop(
                context_runtime=context,
                message_runtime=messages,
                request_builder=_request_builder(messages),
                provider=_AsyncCompleteProvider(),
            ),  # type: ignore[arg-type]
        )
        stream = await agent.run("hello", stream=True)
        async with stream:
            return [event async for event in stream]

    events = asyncio.run(collect())

    assert events[-1] == TurnStreamCompleted(content="async complete")


def test_same_arguments_execute_as_distinct_tool_invocations() -> None:
    calls: list[dict[str, object]] = []

    def lookup(invocation: ToolInvocation) -> str:
        calls.append(dict(invocation.arguments))
        return "sync-ok"

    async def run() -> list[dict[str, object]]:
        context = ContextRuntime()
        messages = MessageRuntime()
        registry = ToolRegistry()
        registry.register(
            RegisteredTool(
                name="lookup",
                description="Lookup.",
                parameters={"type": "object", "properties": {}},
                handler=lookup,
                side_effect_policy=SideEffectPolicy.DEDUPLICATED,
            ),
        )
        router = ToolCallRouter(tool_registry=registry, context_runtime=context)
        loop = QueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages, router),
            provider=_DuplicateToolProvider(),
            tool_call_router=router,
            session_state=SessionState(id="session_duplicate_tool_call"),
        )
        await Agent(loop).run("hello")
        return messages.materialize_active()

    provider_messages = asyncio.run(run())

    assert calls == [{"value": "same"}, {"value": "same"}]
    assert [
        message.content for message in provider_messages if message.role == "tool"
    ] == ["sync-ok", "sync-ok"]


def test_native_provider_cancellation_propagates_and_closes_stream() -> None:
    class BlockingProvider:
        def __init__(self) -> None:
            self.blocked = asyncio.Event()
            self.closed = asyncio.Event()

        async def async_stream(
            self,
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            try:
                yield ProviderStreamStarted(request_id="async_cancel")
                self.blocked.set()
                await asyncio.Event().wait()
            finally:
                self.closed.set()

    async def scenario() -> None:
        messages = MessageRuntime()
        provider = BlockingProvider()
        agent = Agent(
            QueryLoop(
                context_runtime=ContextRuntime(),
                message_runtime=messages,
                request_builder=_request_builder(messages),
                provider=provider,  # type: ignore[arg-type]
            ),
        )
        stream = await agent.run("hello", stream=True)

        async def consume() -> None:
            async for _event in stream:
                pass

        consumer = asyncio.create_task(consume())
        await provider.blocked.wait()
        consumer.cancel("provider consumer cancelled")

        with pytest.raises(asyncio.CancelledError) as caught:
            await consumer

        assert caught.value.args == ("provider consumer cancelled",)
        assert provider.closed.is_set()
        assert stream.closed

    asyncio.run(scenario())


def test_external_close_waits_for_sync_provider_before_releasing_lease() -> None:
    class BlockingSyncProvider:
        timeout_seconds = None

        def __init__(self) -> None:
            self.calls = 0
            self.started = ThreadEvent()
            self.release = ThreadEvent()
            self.finished = ThreadEvent()

        def complete(self, _request: ProviderRequest) -> ProviderResponse:
            self.calls += 1
            if self.calls > 1:
                return ProviderResponse(content="replacement")
            self.started.set()
            self.release.wait()
            self.finished.set()
            return ProviderResponse(content="discarded")

    async def scenario() -> None:
        messages = MessageRuntime()
        provider = BlockingSyncProvider()
        agent = Agent(
            QueryLoop(
                context_runtime=ContextRuntime(),
                message_runtime=messages,
                request_builder=_request_builder(messages),
                provider=provider,
            ),
        )
        stream = await agent.run("hello", stream=True)

        async def consume() -> None:
            async for _event in stream:
                pass

        consumer = asyncio.create_task(consume())
        assert await asyncio.to_thread(provider.started.wait, 5)
        close_task = asyncio.create_task(stream.aclose())
        results: list[object] = []
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(close_task), 0.05)
            with pytest.raises(AgentBusyError):
                await agent.run("replacement", stream=True)
        finally:
            provider.release.set()
            results = await asyncio.gather(
                close_task,
                consumer,
                return_exceptions=True,
            )

        assert results[0] is None
        assert isinstance(results[1], asyncio.CancelledError)
        assert provider.finished.is_set()
        replacement = await agent.run("replacement")
        assert replacement.content == "replacement"

    asyncio.run(scenario())


class _TwoStepProvider:
    timeout_seconds = None

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_1",
                        name=self.tool_name,
                        arguments={"value": "same"},
                    ),
                ],
            )
        return ProviderResponse(content="done")


class _DuplicateToolProvider:
    timeout_seconds = None

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_1",
                        name="lookup",
                        arguments={"value": "same"},
                    ),
                    ProviderToolCall(
                        id="call_2",
                        name="lookup",
                        arguments={"value": "same"},
                    ),
                ],
            )
        return ProviderResponse(content="done")


class _AsyncCompleteProvider:
    timeout_seconds = None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise AssertionError("native async Agent stream must not call sync complete")

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(content="async complete")
