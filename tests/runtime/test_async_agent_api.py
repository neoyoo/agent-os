import asyncio
import threading

from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolRegistry,
)
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider
from agentos.providers.base import ProviderRequest, ProviderResponse
from agentos.runtime import Agent, ProviderRequestBuilder, TurnStreamCompleted
from tests._context_protocol_fixtures import default_context_renderer
from tests.tool_invocation import make_tool_invocation


def build_agent_with_response(content: str) -> Agent:
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": ContextRuntime(),
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": FakeProvider([ProviderResponse(content=content)]),
        },
    )


def test_agent_run_returns_agent_result() -> None:
    async def run() -> None:
        agent = build_agent_with_response("async done")

        result = await agent.run("hello")

        assert result.content == "async done"

    asyncio.run(run())


def test_agent_stream_yields_typed_events() -> None:
    async def run() -> None:
        agent = build_agent_with_response("stream done")

        stream = await agent.run("hello", stream=True)
        async with stream:
            events = [event async for event in stream]

        assert any(isinstance(event, TurnStreamCompleted) for event in events)

    asyncio.run(run())


def test_agent_runs_sync_provider_without_blocking_event_loop() -> None:
    class BlockingSyncProvider:
        timeout_seconds = None

        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def complete(self, request: ProviderRequest) -> ProviderResponse:
            self.started.set()
            self.release.wait(timeout=1)
            return ProviderResponse(content="sync provider done")

    async def run() -> None:
        context = ContextRuntime()
        messages = MessageRuntime()
        provider = BlockingSyncProvider()
        agent = Agent(
            query_loop_kwargs={
                "context_runtime": context,
                "message_runtime": messages,
                "request_builder": ProviderRequestBuilder(
                    context_renderer=default_context_renderer(),
                    message_runtime=messages,
                    tools=[],
                ),
                "provider": provider,
            },
        )

        pending = asyncio.create_task(agent.run("hello"))
        assert await asyncio.to_thread(provider.started.wait, 1) is True
        marker_ran = False

        async def marker() -> None:
            nonlocal marker_ran
            await asyncio.sleep(0)
            marker_ran = True

        await marker()
        provider.release.set()
        result = await pending

        assert result.content == "sync provider done"
        assert marker_ran is True

    asyncio.run(run())


def test_tool_call_router_async_executes_external_tool() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _invocation: "async tool result",
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    router = ToolCallRouter(tool_registry=registry, context_runtime=ContextRuntime())

    result = asyncio.run(
        router.execute(
            make_tool_invocation("lookup", {}),
        ),
    )

    assert result.tool_call_id == "call_lookup"
    assert result.content == "async tool result"
