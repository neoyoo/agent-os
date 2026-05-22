import asyncio
import threading

import pytest

from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderToolCall
from agentos.providers.base import ProviderRequest, ProviderResponse
from agentos.providers.stream import (
    ProviderStreamCompleted,
    ProviderStreamOptions,
    ProviderStreamStarted,
)
from agentos.runtime import (
    Agent,
    AsyncQueryLoop,
    ProviderRequestBuilder,
    TurnStreamCompleted,
    TurnStreamStarted,
)
from tests.multi.helpers import build_agent_with_response


def test_agent_async_run_returns_agent_result() -> None:
    agent = build_agent_with_response("async done")

    result = asyncio.run(agent.async_run("hello"))

    assert result.content == "async done"


def test_agent_async_stream_yields_typed_events() -> None:
    agent = build_agent_with_response("stream done")

    async def collect() -> list[object]:
        return [event async for event in agent.async_stream("hello")]

    events = asyncio.run(collect())

    assert any(isinstance(event, TurnStreamCompleted) for event in events)


def test_async_query_loop_runs_sync_provider_without_blocking_event_loop() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    provider = FakeProvider(["async loop done"])
    loop = AsyncQueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=[],
        ),
        provider=provider,
    )

    async def run_with_marker() -> tuple[str, bool]:
        marker_ran = False

        async def marker() -> None:
            nonlocal marker_ran
            await asyncio.sleep(0)
            marker_ran = True

        result, _ = await asyncio.gather(loop.run_turn("hello"), marker())
        return result, marker_ran

    result, marker_ran = asyncio.run(run_with_marker())

    assert result == "async loop done"
    assert marker_ran is True


def test_tool_call_router_async_executes_external_tool() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: "async tool result",
        ),
    )
    router = ToolCallRouter(tool_registry=registry, context_runtime=ContextRuntime())

    result = asyncio.run(
        router.async_execute_tool_call(
            ProviderToolCall(id="call_1", name="lookup", arguments={}),
        ),
    )

    assert result.tool_call_id == "call_1"
    assert result.content == "async tool result"


def test_agent_can_use_explicit_async_query_loop() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    async_loop = AsyncQueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=[],
        ),
        provider=FakeProvider(["explicit async loop"]),
    )
    agent = Agent(query_loop=async_loop.sync_loop)

    result = asyncio.run(agent.async_run("hello"))

    assert result.content == "explicit async loop"


def test_agent_async_stream_cancellation_waits_for_worker_to_finish() -> None:
    release_worker = threading.Event()
    worker_blocked = threading.Event()

    class BlockingLoop:
        interrupted = False

        def request_interrupt(self) -> None:
            self.interrupted = True

        def run_turn_stream(self, user_message: str, options=None):
            yield TurnStreamStarted(user_message=user_message)
            worker_blocked.set()
            release_worker.wait(timeout=1)
            yield TurnStreamCompleted(content="late")

    async def cancel_after_first_event() -> tuple[bool, bool]:
        loop = BlockingLoop()
        agent = Agent(query_loop=loop)  # type: ignore[arg-type]
        stream = agent.async_stream("hello")

        first = await anext(stream)
        assert first == TurnStreamStarted(user_message="hello")
        assert await asyncio.to_thread(worker_blocked.wait, 1) is True

        pending_next = asyncio.create_task(anext(stream))
        async with asyncio.timeout(1):
            while agent._current_async_task is not pending_next:
                await asyncio.sleep(0)
        pending_next.cancel()
        await asyncio.sleep(0.05)
        was_done_before_interrupt = pending_next.done()
        release_worker.set()

        with pytest.raises(asyncio.CancelledError):
            await pending_next
        return was_done_before_interrupt, loop.interrupted

    done_before_interrupt, interrupted = asyncio.run(cancel_after_first_event())

    assert done_before_interrupt is False
    assert interrupted is True


def test_agent_async_stream_close_waits_for_worker_to_finish() -> None:
    release_worker = threading.Event()
    worker_blocked = threading.Event()

    class BlockingLoop:
        interrupted = False

        def request_interrupt(self) -> None:
            self.interrupted = True

        def run_turn_stream(self, user_message: str, options=None):
            yield TurnStreamStarted(user_message=user_message)
            worker_blocked.set()
            release_worker.wait(timeout=1)
            yield TurnStreamCompleted(content="late")

    async def close_after_first_event() -> tuple[bool, bool]:
        loop = BlockingLoop()
        agent = Agent(query_loop=loop)  # type: ignore[arg-type]
        stream = agent.async_stream("hello")

        first = await anext(stream)
        assert first == TurnStreamStarted(user_message="hello")
        assert await asyncio.to_thread(worker_blocked.wait, 1) is True

        close_task = asyncio.create_task(stream.aclose())
        async with asyncio.timeout(1):
            while agent._current_async_task is not close_task:
                await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        was_done_before_release = close_task.done()
        release_worker.set()
        await close_task
        return was_done_before_release, loop.interrupted

    done_before_release, interrupted = asyncio.run(close_after_first_event())

    assert done_before_release is False
    assert interrupted is True


def test_agent_async_stream_cancels_running_async_provider_task() -> None:
    provider_started = threading.Event()
    provider_cancelled = threading.Event()

    class BlockingAsyncProvider:
        def complete(self, request: ProviderRequest) -> ProviderResponse:
            raise AssertionError("async Agent stream must not use sync complete")

        async def async_stream(
            self,
            request: ProviderRequest,
            options: ProviderStreamOptions,
        ):
            provider_started.set()
            yield ProviderStreamStarted(
                request_id="async_request",
                thinking_requested=options.thinking,
                thinking_supported=False,
            )
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                provider_cancelled.set()
                raise
            yield ProviderStreamCompleted(
                request_id="async_request",
                response=ProviderResponse(content="late"),
            )

    async def cancel_after_provider_starts() -> bool:
        context = ContextRuntime()
        messages = MessageRuntime()
        agent = Agent(
            query_loop=AsyncQueryLoop(
                context_runtime=context,
                message_runtime=messages,
                request_builder=ProviderRequestBuilder(
                    context_renderer=ContextRenderer(),
                    message_runtime=messages,
                    tools=[],
                ),
                provider=BlockingAsyncProvider(),  # type: ignore[arg-type]
            ).sync_loop,
        )
        stream = agent.async_stream("hello")

        first = await anext(stream)
        assert isinstance(first, TurnStreamStarted)

        pending_next = asyncio.create_task(anext(stream))
        started = await asyncio.to_thread(provider_started.wait, 1)
        assert started is True
        pending_next.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending_next
        return await asyncio.to_thread(provider_cancelled.wait, 1)

    assert asyncio.run(cancel_after_provider_starts()) is True


def test_agent_async_stream_uses_async_complete_when_stream_is_unavailable() -> None:
    class AsyncCompleteProvider:
        complete_called = False

        def complete(self, request: ProviderRequest) -> ProviderResponse:
            self.complete_called = True
            raise AssertionError("async Agent stream must not use sync complete")

        async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
            return ProviderResponse(content="async complete")

    async def collect() -> tuple[list[object], bool]:
        context = ContextRuntime()
        messages = MessageRuntime()
        provider = AsyncCompleteProvider()
        agent = Agent(
            query_loop=AsyncQueryLoop(
                context_runtime=context,
                message_runtime=messages,
                request_builder=ProviderRequestBuilder(
                    context_renderer=ContextRenderer(),
                    message_runtime=messages,
                    tools=[],
                ),
                provider=provider,  # type: ignore[arg-type]
            ).sync_loop,
        )
        events = [event async for event in agent.async_stream("hello")]
        return events, provider.complete_called

    events, complete_called = asyncio.run(collect())

    assert complete_called is False
    assert events[-1] == TurnStreamCompleted(content="async complete")
