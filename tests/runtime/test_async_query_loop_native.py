import asyncio

import pytest

from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import (
    ProviderContentDelta,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderToolCall,
    ProviderRequest,
)
from agentos.runtime import (
    Agent,
    AsyncQueryLoop,
    ProviderRequestBuilder,
    QueryLoop,
    TurnStreamCompleted,
)


def _request_builder(
    messages: MessageRuntime,
    router: ToolCallRouter | None = None,
) -> ProviderRequestBuilder:
    return ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[] if router is None else router.tool_specs(),
    )


def test_async_handler_awaited_not_returned_as_coroutine() -> None:
    async def lookup(arguments: dict[str, object]) -> str:
        await asyncio.sleep(0)
        return "async-ok"

    async def run() -> tuple[str, list[dict[str, object]]]:
        context = ContextRuntime()
        messages = MessageRuntime()
        registry = ToolRegistry()
        registry.register(
            RegisteredTool(
                name="lookup",
                description="Lookup.",
                parameters={"type": "object", "properties": {}},
                handler=lookup,  # type: ignore[arg-type]
            ),
        )
        router = ToolCallRouter(tool_registry=registry, context_runtime=context)
        loop = AsyncQueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages, router),
            provider=_TwoStepProvider("lookup"),
            tool_call_router=router,
        )
        result = await loop.run_turn("hello")
        return result, messages.materialize_provider_messages()

    result, provider_messages = asyncio.run(run())

    assert result == "done"
    assert provider_messages[-2]["role"] == "tool"
    assert provider_messages[-2]["content"] == "async-ok"


def test_sync_handler_still_works_in_async_loop() -> None:
    calls: list[dict[str, object]] = []

    def lookup(arguments: dict[str, object]) -> str:
        calls.append(arguments)
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
            ),
        )
        router = ToolCallRouter(tool_registry=registry, context_runtime=context)
        loop = AsyncQueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages, router),
            provider=_TwoStepProvider("lookup"),
            tool_call_router=router,
        )
        return await loop.run_turn("hello")

    result = asyncio.run(run())

    assert result == "done"
    assert calls == [{"value": "same"}]


def test_sync_loop_rejects_async_handler() -> None:
    async def lookup(arguments: dict[str, object]) -> str:
        return "async-ok"

    context = ContextRuntime()
    messages = MessageRuntime()
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup.",
            parameters={"type": "object", "properties": {}},
            handler=lookup,  # type: ignore[arg-type]
        ),
    )
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=_request_builder(messages, router),
        provider=_TwoStepProvider("lookup"),
        tool_call_router=router,
    )

    with pytest.raises(RuntimeError, match="async handler requires AsyncQueryLoop"):
        loop.run_turn("hello")


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
        loop = AsyncQueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages),
            provider=provider,  # type: ignore[arg-type]
        )
        events = [event async for event in loop.run_turn_stream("hello")]
        return events, provider.complete_called

    events, complete_called = asyncio.run(collect())

    assert complete_called is False
    assert events[-1] == TurnStreamCompleted(content="async")


def test_agent_async_stream_uses_native_async_query_loop() -> None:
    async def collect() -> list[object]:
        context = ContextRuntime()
        messages = MessageRuntime()
        agent = Agent(
            query_loop=AsyncQueryLoop(
                context_runtime=context,
                message_runtime=messages,
                request_builder=_request_builder(messages),
                provider=_AsyncCompleteProvider(),
            ),  # type: ignore[arg-type]
        )
        return [event async for event in agent.async_stream("hello")]

    events = asyncio.run(collect())

    assert events[-1] == TurnStreamCompleted(content="async complete")


def test_duplicate_tool_call_suppression_matches_sync_loop() -> None:
    calls: list[dict[str, object]] = []

    def lookup(arguments: dict[str, object]) -> str:
        calls.append(arguments)
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
            ),
        )
        router = ToolCallRouter(tool_registry=registry, context_runtime=context)
        loop = AsyncQueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=_request_builder(messages, router),
            provider=_DuplicateToolProvider(),
            tool_call_router=router,
        )
        await loop.run_turn("hello")
        return messages.materialize_provider_messages()

    provider_messages = asyncio.run(run())

    assert calls == [{"value": "same"}]
    assert "duplicate tool call ignored" in str(provider_messages[-2]["content"])


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
