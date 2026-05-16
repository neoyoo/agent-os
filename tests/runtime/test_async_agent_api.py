import asyncio
import threading

import pytest

from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderToolCall
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

    class BlockingLoop:
        interrupted = False

        def request_interrupt(self) -> None:
            self.interrupted = True

        def run_turn_stream(self, user_message: str, options=None):
            yield TurnStreamStarted(user_message=user_message)
            release_worker.wait(timeout=1)
            yield TurnStreamCompleted(content="late")

    async def cancel_after_first_event() -> tuple[bool, bool]:
        loop = BlockingLoop()
        agent = Agent(query_loop=loop)  # type: ignore[arg-type]
        stream = agent.async_stream("hello")

        first = await anext(stream)
        assert first == TurnStreamStarted(user_message="hello")

        pending_next = asyncio.create_task(anext(stream))
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
