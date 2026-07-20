import asyncio
from threading import Event as ThreadEvent

import pytest

from agentos import Agent
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolConcurrencyPolicy,
    ToolInvocation,
    ToolRegistry,
)
from agentos.capabilities.executor import ToolExecutionError
from agentos.context import ContextRuntime
from agentos.events import (
    EventBus,
    ToolCallRequestedEvent,
    ToolResultAppendedEvent,
    ToolResultCappedEvent,
)
from agentos.hooks import HookContext, HookManager, HookRegistry
from agentos.messages import MessageRuntime, StoredMessage
from agentos.policies import ToolResultBudget
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import ProviderRequestBuilder, QueryLoop, SessionState
from agentos.runtime.errors import AgentBusyError
from agentos.runtime.stream_events import (
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
)
from agentos.tokens import HeuristicTokenCounter
from tests._context_protocol_fixtures import default_context_renderer


class FailingSecondToolResultRuntime(MessageRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.append_attempts = 0

    def append_tool_result(
        self,
        tool_call_id: str,
        content: str,
    ) -> StoredMessage:
        self.append_attempts += 1
        if self.append_attempts == 2:
            raise RuntimeError("second tool result append failed")
        return super().append_tool_result(tool_call_id, content)


def _agent(
    tools: list[RegisteredTool],
    response: ProviderResponse,
    *,
    hook_manager: HookManager | None = None,
    message_runtime: MessageRuntime | None = None,
) -> tuple[Agent, MessageRuntime, EventBus, FakeProvider]:
    context = ContextRuntime()
    messages = message_runtime or MessageRuntime()
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    provider = FakeProvider([response, ProviderResponse("done")])
    event_bus = EventBus()
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
        event_bus=event_bus,
        hook_manager=hook_manager,
        session_state=SessionState(id="session_tool_batch_atomicity"),
    )
    return Agent(loop), messages, event_bus, provider


def test_same_arguments_use_independent_invocations_in_one_batch() -> None:
    calls = 0

    async def lookup(_invocation: ToolInvocation) -> str:
        nonlocal calls
        calls += 1
        return "value"

    agent, _messages, event_bus, provider = _agent(
        [
            RegisteredTool(
                "lookup",
                "lookup",
                {"type": "object"},
                lookup,
                side_effect_policy=SideEffectPolicy.DEDUPLICATED,
                concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
            ),
        ],
        ProviderResponse(
            tool_calls=(
                ProviderToolCall("call_1", "lookup", {"key": "same"}),
                ProviderToolCall("call_2", "lookup", {"key": "same"}),
            ),
        ),
    )

    outcome = asyncio.run(agent.run("hello"))

    assert outcome.content == "done"
    assert calls == 2
    assert [
        event.tool_call_id
        for event in event_bus.events
        if isinstance(event, ToolCallRequestedEvent)
    ] == ["call_1", "call_2"]
    assert [message.tool_call_id for message in provider.requests[1].messages[-2:]] == [
        "call_1",
        "call_2",
    ]


def test_batch_failure_appends_no_partial_tool_results_and_clears_window() -> None:
    async def succeed(_invocation: ToolInvocation) -> str:
        return "ok"

    async def fail(_invocation: ToolInvocation) -> str:
        raise RuntimeError("tool failed")

    agent, messages, event_bus, _provider = _agent(
        [
            RegisteredTool(
                "first",
                "first",
                {"type": "object"},
                succeed,
                side_effect_policy=SideEffectPolicy.PURE,
                concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
            ),
            RegisteredTool(
                "second",
                "second",
                {"type": "object"},
                fail,
                side_effect_policy=SideEffectPolicy.PURE,
                concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
            ),
        ],
        ProviderResponse(
            tool_calls=(
                ProviderToolCall("call_1", "first", {}),
                ProviderToolCall("call_2", "second", {}),
            ),
        ),
    )

    with pytest.raises(ToolExecutionError, match="^tool execution failed$"):
        asyncio.run(agent.run("hello"))

    assert [message.role for message in messages.materialize_active()] == ["user"]
    assert not any(
        isinstance(event, ToolResultAppendedEvent) for event in event_bus.events
    )


def test_tool_handler_failure_does_not_expose_argument_values() -> None:
    async def scenario() -> None:
        secret = "secret-tool-argument-must-not-leak"

        async def fail(invocation: ToolInvocation) -> str:
            raise RuntimeError(
                f"backend rejected {invocation.arguments['api_key']}",
            )

        agent, _messages, _event_bus, _provider = _agent(
            [
                RegisteredTool(
                    "lookup",
                    "lookup",
                    {"type": "object"},
                    fail,
                    side_effect_policy=SideEffectPolicy.PURE,
                ),
            ],
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall("call_1", "lookup", {"api_key": secret}),
                ),
            ),
        )
        stream = await agent.run("hello", stream=True)
        streamed_events: list[object] = []

        with pytest.raises(
            ToolExecutionError,
            match="^tool execution failed$",
        ) as caught:
            async for event in stream:
                streamed_events.append(event)

        failed = next(
            event
            for event in streamed_events
            if isinstance(event, ToolStreamFailed)
        )
        assert type(failed.error) is ToolExecutionError
        assert str(failed.error) == "tool execution failed"
        assert secret not in str(failed.error)
        assert secret not in str(caught.value)
        assert failed.error.__context__ is None
        assert caught.value.__context__ is None

    asyncio.run(scenario())


def test_before_tool_hook_failure_rolls_back_assistant_batch() -> None:
    error = RuntimeError("before hook failed")
    registry = HookRegistry()

    def fail_before_tool(_context: object) -> None:
        raise error

    registry.register(
        "before_tool_call",
        fail_before_tool,
        failure_policy="raise",
    )
    agent, messages, event_bus, _provider = _agent(
        [
            RegisteredTool(
                "lookup",
                "lookup",
                {"type": "object"},
                lambda _invocation: "unused",
                side_effect_policy=SideEffectPolicy.PURE,
            ),
        ],
        ProviderResponse(
            tool_calls=(ProviderToolCall("call_1", "lookup", {}),),
        ),
        hook_manager=HookManager(registry),
    )

    with pytest.raises(
        ToolExecutionError,
        match="^tool execution failed$",
    ):
        asyncio.run(agent.run("hello"))

    assert [message.role for message in messages.materialize_active()] == ["user"]
    assert not any(
        isinstance(event, ToolResultAppendedEvent) for event in event_bus.events
    )


def test_stream_close_during_tool_batch_rolls_back_assistant_batch() -> None:
    async def scenario() -> None:
        agent, messages, _event_bus, _provider = _agent(
            [
                RegisteredTool(
                    "lookup",
                    "lookup",
                    {"type": "object"},
                    lambda _invocation: "unused",
                    side_effect_policy=SideEffectPolicy.PURE,
                ),
            ],
            ProviderResponse(
                tool_calls=(ProviderToolCall("call_1", "lookup", {}),),
            ),
        )

        stream = await agent.run("hello", stream=True)
        while True:
            event = await anext(stream)
            if isinstance(event, ToolStreamStarted):
                break
        await stream.aclose()

        assert [message.role for message in messages.materialize_active()] == ["user"]
        replacement = await agent.run("replacement")
        assert replacement.content == "done"

    asyncio.run(scenario())


def test_consumer_cancellation_during_tool_execution_rolls_back_batch() -> None:
    async def scenario() -> None:
        tool_started = asyncio.Event()

        async def block(_invocation: ToolInvocation) -> str:
            tool_started.set()
            await asyncio.Event().wait()
            return "unreachable"

        agent, messages, _event_bus, _provider = _agent(
            [
                RegisteredTool(
                    "lookup",
                    "lookup",
                    {"type": "object"},
                    block,
                    side_effect_policy=SideEffectPolicy.PURE,
                ),
            ],
            ProviderResponse(
                tool_calls=(ProviderToolCall("call_1", "lookup", {}),),
            ),
        )
        stream = await agent.run("hello", stream=True)

        async def consume() -> None:
            async for _event in stream:
                pass

        consumer = asyncio.create_task(consume())
        await tool_started.wait()
        consumer.cancel("consumer cancelled")
        with pytest.raises(asyncio.CancelledError) as caught:
            await consumer

        assert caught.value.args == ("consumer cancelled",)
        assert [message.role for message in messages.materialize_active()] == ["user"]
        replacement = await agent.run("replacement")
        assert replacement.content == "done"

    asyncio.run(scenario())


def test_external_close_waits_for_sync_tool_and_preserves_batch_rollback() -> None:
    async def scenario() -> None:
        tool_started = ThreadEvent()
        release_tool = ThreadEvent()
        tool_finished = ThreadEvent()

        def block(_invocation: ToolInvocation) -> str:
            tool_started.set()
            release_tool.wait()
            tool_finished.set()
            return "discarded"

        agent, messages, _event_bus, _provider = _agent(
            [
                RegisteredTool(
                    "lookup",
                    "lookup",
                    {"type": "object"},
                    block,
                    side_effect_policy=SideEffectPolicy.PURE,
                ),
            ],
            ProviderResponse(
                tool_calls=(ProviderToolCall("call_1", "lookup", {}),),
            ),
        )
        stream = await agent.run("hello", stream=True)

        async def consume() -> None:
            async for _event in stream:
                pass

        consumer = asyncio.create_task(consume())
        assert await asyncio.to_thread(tool_started.wait, 5)
        close_task = asyncio.create_task(stream.aclose())
        results: list[object] = []
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(close_task), 0.05)
            with pytest.raises(AgentBusyError):
                await agent.run("replacement", stream=True)
            assert [message.role for message in messages.materialize_active()] == [
                "user",
            ]
        finally:
            release_tool.set()
            results = await asyncio.gather(
                close_task,
                consumer,
                return_exceptions=True,
            )

        assert results[0] is None
        assert isinstance(results[1], asyncio.CancelledError)
        assert tool_finished.is_set()
        assert [message.role for message in messages.materialize_active()] == ["user"]
        replacement = await agent.run("replacement")
        assert replacement.content == "done"

    asyncio.run(scenario())


def test_tool_result_append_failure_rolls_back_all_active_batch_refs() -> None:
    async def scenario() -> None:
        messages = FailingSecondToolResultRuntime()
        agent, _messages, event_bus, _provider = _agent(
            [
                RegisteredTool(
                    "first",
                    "first",
                    {"type": "object"},
                    lambda _invocation: "one",
                    side_effect_policy=SideEffectPolicy.PURE,
                ),
                RegisteredTool(
                    "second",
                    "second",
                    {"type": "object"},
                    lambda _invocation: "two",
                    side_effect_policy=SideEffectPolicy.PURE,
                ),
            ],
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall("call_1", "first", {}),
                    ProviderToolCall("call_2", "second", {}),
                ),
            ),
            message_runtime=messages,
        )
        stream = await agent.run("hello", stream=True)
        streamed_events: list[object] = []

        with pytest.raises(RuntimeError, match="second tool result append failed"):
            async for event in stream:
                streamed_events.append(event)

        assert [message.role for message in messages.materialize_active()] == ["user"]
        assert [message.role for message in messages.store.all()] == [
            "user",
            "assistant",
            "tool",
        ]
        assert not any(
            isinstance(event, ToolResultAppendedEvent) for event in event_bus.events
        )
        assert not any(
            isinstance(event, ToolStreamCompleted) for event in streamed_events
        )

    asyncio.run(scenario())


def test_later_after_tool_hook_failure_emits_no_earlier_cap_fact() -> None:
    error = RuntimeError("after hook failed")
    registry = HookRegistry()

    def fail_second(context: HookContext) -> None:
        result = context.payload["result"]
        if getattr(result, "tool_call_id", None) == "call_2":
            raise error

    registry.register("after_tool_call", fail_second, failure_policy="raise")
    agent, messages, event_bus, _provider = _agent(
        [
            RegisteredTool(
                "first",
                "first",
                {"type": "object"},
                lambda _invocation: "x" * 100,
                side_effect_policy=SideEffectPolicy.PURE,
            ),
            RegisteredTool(
                "second",
                "second",
                {"type": "object"},
                lambda _invocation: "two",
                side_effect_policy=SideEffectPolicy.PURE,
            ),
        ],
        ProviderResponse(
            tool_calls=(
                ProviderToolCall("call_1", "first", {}),
                ProviderToolCall("call_2", "second", {}),
            ),
        ),
        hook_manager=HookManager(registry),
    )
    agent.query_loop.tool_result_budget = ToolResultBudget(default_max_tokens=5)
    agent.query_loop.token_counter = HeuristicTokenCounter(char_per_token=1)

    with pytest.raises(
        ToolExecutionError,
        match="^tool execution failed$",
    ):
        asyncio.run(agent.run("hello"))

    assert [message.role for message in messages.materialize_active()] == ["user"]
    assert not any(
        isinstance(event, (ToolResultCappedEvent, ToolResultAppendedEvent))
        for event in event_bus.events
    )
