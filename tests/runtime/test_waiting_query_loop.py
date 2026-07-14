import asyncio

import pytest

from agentos import Agent
from agentos.capabilities import (
    RegisteredTool,
    ToolCallRouter,
    ToolConcurrencyPolicy,
    ToolRegistry,
    WaitRequest,
)
from agentos.context import ContextRuntime
from agentos.events import (
    EventBus,
    ToolResultAppendedEvent,
    ToolResultCappedEvent,
    TurnCompletedEvent,
)
from agentos.hooks import HookContext, HookManager, HookRegistry
from agentos.messages import MessageRuntime
from agentos.policies import ToolResultBudget
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    AgentResult,
    AgentWaiting,
    ProviderRequestBuilder,
    QueryLoop,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamCompleted,
    TurnStreamWaiting,
    WaitReason,
)
from agentos.runtime.errors import WaitingUnsupportedError
from agentos.runtime.session import SessionState
from agentos.runtime.tool_scheduler import ToolCallScheduler
from agentos.runtime.turn import TurnState
from agentos.runtime.waiting import WaitingCommit, WaitingRuntime
from agentos.tokens import HeuristicTokenCounter
from tests._context_protocol_fixtures import default_context_renderer
from tests.runtime._query_loop_contract_fixtures import RecordingWaitingRuntime


class RecordingSessionState(SessionState):
    def __init__(self) -> None:
        super().__init__("session_1")
        self.turns: list[TurnState] = []

    def new_turn(self, user_input: str) -> TurnState:
        turn = super().new_turn(user_input)
        self.turns.append(turn)
        return turn


class FailingWaitingRuntime:
    def __init__(self, order: list[str], error: RuntimeError) -> None:
        self.order = order
        self.error = error

    async def commit_waiting(
        self,
        *,
        turn_id: str,
        reason: WaitReason,
    ) -> WaitingCommit:
        self.order.append("commit_attempted")
        raise self.error


def _agent(
    *,
    tools: list[RegisteredTool],
    responses: list[ProviderResponse],
    waiting_runtime: WaitingRuntime | None = None,
    hook_manager: HookManager | None = None,
) -> tuple[Agent, RecordingSessionState, MessageRuntime, EventBus]:
    context = ContextRuntime()
    messages = MessageRuntime()
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    session = RecordingSessionState()
    event_bus = EventBus()
    return (
        Agent(
            QueryLoop(
                context_runtime=context,
                message_runtime=messages,
                request_builder=ProviderRequestBuilder(
                    context_renderer=default_context_renderer(),
                    message_runtime=messages,
                    tools=router.tool_specs(),
                ),
                provider=FakeProvider(responses),
                tool_call_router=router,
                session_state=session,
                event_bus=event_bus,
                waiting_runtime=waiting_runtime,
                hook_manager=hook_manager,
            ),
        ),
        session,
        messages,
        event_bus,
    )


def _wait_tool(
    reason: WaitReason,
    *,
    name: str = "request_waiting",
    concurrency_policy: ToolConcurrencyPolicy = ToolConcurrencyPolicy.PARALLEL_SAFE,
) -> RegisteredTool:
    async def request_waiting(_arguments: dict[str, object]) -> WaitRequest:
        return WaitRequest(reason)

    return RegisteredTool(
        name,
        "Request authoritative waiting.",
        {"type": "object"},
        request_waiting,
        concurrency_policy=concurrency_policy,
    )


def test_streaming_wait_commits_before_event_rolls_back_batch_and_releases_lease() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        order: list[str] = []
        after_hook_calls: list[str] = []
        hooks = HookRegistry()

        def observe_after_tool(context: HookContext) -> None:
            result = context.payload["result"]
            after_hook_calls.append(result.tool_call_id)  # type: ignore[attr-defined]

        hooks.register("after_tool_call", observe_after_tool)

        async def completed_peer(_arguments: dict[str, object]) -> str:
            return "must not be appended"

        agent, session, messages, event_bus = _agent(
            tools=[
                RegisteredTool(
                    "completed_peer",
                    "Complete alongside a waiting request.",
                    {"type": "object"},
                    completed_peer,
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
                _wait_tool(reason),
            ],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "completed_peer", {}),
                        ProviderToolCall("call_2", "request_waiting", {}),
                    ),
                ),
                ProviderResponse("replacement completed"),
            ],
            waiting_runtime=RecordingWaitingRuntime(order, run_id="run_1"),
            hook_manager=HookManager(hooks),
        )

        stream = await agent.run("hello", stream=True)
        events: list[object] = []
        async for event in stream:
            events.append(event)
            if isinstance(event, TurnStreamWaiting):
                order.append("waiting_observed")

        assert stream.closed
        assert order == ["state_committed", "waiting_observed"]
        assert session.turns[0].status == "waiting"
        assert events[-1] == TurnStreamWaiting("run_1", reason)
        assert after_hook_calls == ["call_1"]
        assert [
            event.tool_call_id for event in events if isinstance(event, ToolStreamStarted)
        ] == ["call_1", "call_2"]
        assert [
            event.tool_call_id for event in events if isinstance(event, ToolStreamCompleted)
        ] == ["call_1"]
        assert not any(isinstance(event, (ToolStreamFailed, TurnStreamCompleted)) for event in events)
        assert [message.role for message in messages.materialize_active()] == ["user"]
        assert [message.role for message in messages.store.all()] == [
            "user",
            "assistant",
        ]
        assert not any(
            isinstance(event, (ToolResultAppendedEvent, TurnCompletedEvent))
            for event in event_bus.events
        )

        replacement = await agent.run("replacement")

        assert replacement == AgentResult("replacement completed")
        assert session.turns[1].status == "completed"

    asyncio.run(run())


def test_waiting_peer_result_is_capped_before_stream_without_append() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        raw_content = "x" * 100

        agent, _session, messages, event_bus = _agent(
            tools=[
                RegisteredTool(
                    "oversized_peer",
                    "Return an oversized result beside a waiting request.",
                    {"type": "object"},
                    lambda _arguments: raw_content,
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
                _wait_tool(reason),
            ],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_peer", "oversized_peer", {}),
                        ProviderToolCall("call_wait", "request_waiting", {}),
                    ),
                ),
            ],
            waiting_runtime=RecordingWaitingRuntime([], run_id="run_capped"),
        )
        agent.query_loop.tool_result_budget = ToolResultBudget(default_max_tokens=5)
        agent.query_loop.token_counter = HeuristicTokenCounter(char_per_token=1)

        stream = await agent.run("hello", stream=True)
        events = [event async for event in stream]
        completed = [event for event in events if isinstance(event, ToolStreamCompleted)]

        assert len(completed) == 1
        assert raw_content not in completed[0].content
        assert "tool result omitted" in completed[0].content
        assert any(isinstance(event, ToolResultCappedEvent) for event in event_bus.events)
        assert not any(
            isinstance(event, ToolResultAppendedEvent) for event in event_bus.events
        )
        assert [message.role for message in messages.materialize_active()] == ["user"]

    asyncio.run(run())


def test_default_exclusive_wait_stops_later_side_effect_tool() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        side_effects: list[str] = []

        def request_waiting(_arguments: dict[str, object]) -> WaitRequest:
            return WaitRequest(reason)

        def later_side_effect(_arguments: dict[str, object]) -> str:
            side_effects.append("ran")
            return "unexpected"

        agent, _session, _messages, _event_bus = _agent(
            tools=[
                RegisteredTool(
                    "request_waiting",
                    "Request waiting.",
                    {"type": "object"},
                    request_waiting,
                ),
                RegisteredTool(
                    "later_side_effect",
                    "Must not run after waiting.",
                    {"type": "object"},
                    later_side_effect,
                ),
            ],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "request_waiting", {}),
                        ProviderToolCall("call_2", "later_side_effect", {}),
                    ),
                ),
            ],
            waiting_runtime=RecordingWaitingRuntime([], run_id="run_exclusive"),
        )

        outcome = await agent.run("hello")

        assert outcome == AgentWaiting("run_exclusive", reason)
        assert side_effects == []

    asyncio.run(run())


def test_parallel_wait_does_not_start_queued_or_later_segment_tools() -> None:
    async def run() -> None:
        reason = WaitReason("timer", "timer_1")
        running_started = asyncio.Event()
        release_running = asyncio.Event()
        started: list[str] = []

        async def running_peer(_arguments: dict[str, object]) -> str:
            started.append("running_peer")
            running_started.set()
            await release_running.wait()
            return "peer completed"

        async def queued_side_effect(_arguments: dict[str, object]) -> str:
            started.append("queued_side_effect")
            return "unexpected"

        def later_exclusive(_arguments: dict[str, object]) -> str:
            started.append("later_exclusive")
            return "unexpected"

        agent, _session, _messages, _event_bus = _agent(
            tools=[
                _wait_tool(reason),
                RegisteredTool(
                    "running_peer",
                    "Already running peer.",
                    {"type": "object"},
                    running_peer,
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
                RegisteredTool(
                    "queued_side_effect",
                    "Queued side effect.",
                    {"type": "object"},
                    queued_side_effect,
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
                RegisteredTool(
                    "later_exclusive",
                    "Later exclusive segment.",
                    {"type": "object"},
                    later_exclusive,
                ),
            ],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_wait", "request_waiting", {}),
                        ProviderToolCall("call_running", "running_peer", {}),
                        ProviderToolCall("call_queued", "queued_side_effect", {}),
                        ProviderToolCall("call_later", "later_exclusive", {}),
                    ),
                ),
            ],
            waiting_runtime=RecordingWaitingRuntime([], run_id="run_parallel"),
        )
        agent.query_loop.tool_scheduler = ToolCallScheduler(max_parallel_calls=2)
        stream = await agent.run("hello", stream=True)
        events: list[object] = []

        async def consume() -> None:
            async for event in stream:
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(2):
                await running_started.wait()
                await asyncio.sleep(0)
                assert started == ["running_peer"]
                release_running.set()
                await consumer
        finally:
            release_running.set()
            if not consumer.done():
                consumer.cancel()

        assert events[-1] == TurnStreamWaiting("run_parallel", reason)
        assert [
            event.tool_call_id for event in events if isinstance(event, ToolStreamStarted)
        ] == ["call_wait", "call_running"]

    asyncio.run(run())


def test_tool_error_wins_over_wait_request_without_committing_waiting() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        order: list[str] = []
        error = RuntimeError("peer failed")
        wait_returned = asyncio.Event()

        async def request_waiting(_arguments: dict[str, object]) -> WaitRequest:
            wait_returned.set()
            return WaitRequest(reason)

        async def fail_peer(_arguments: dict[str, object]) -> str:
            await wait_returned.wait()
            raise error

        agent, _session, _messages, _event_bus = _agent(
            tools=[
                RegisteredTool(
                    "request_waiting",
                    "Request waiting.",
                    {"type": "object"},
                    request_waiting,
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
                RegisteredTool(
                    "fail_peer",
                    "Fail after wait request.",
                    {"type": "object"},
                    fail_peer,
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
            ],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_wait", "request_waiting", {}),
                        ProviderToolCall("call_error", "fail_peer", {}),
                    ),
                ),
            ],
            waiting_runtime=RecordingWaitingRuntime(order, run_id="unused"),
        )

        with pytest.raises(RuntimeError) as caught:
            await agent.run("hello")

        assert caught.value is error
        assert order == []

    asyncio.run(run())


def test_multiple_wait_requests_choose_lowest_provider_index() -> None:
    async def run() -> None:
        first = WaitReason("human_input", "approval_first")
        second = WaitReason("timer", "timer_second")
        release_first = asyncio.Event()

        async def wait_first(_arguments: dict[str, object]) -> WaitRequest:
            await release_first.wait()
            return WaitRequest(first)

        async def wait_second(_arguments: dict[str, object]) -> WaitRequest:
            release_first.set()
            return WaitRequest(second)

        agent, _session, _messages, _event_bus = _agent(
            tools=[
                RegisteredTool(
                    "wait_first",
                    "First provider wait.",
                    {"type": "object"},
                    wait_first,
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
                RegisteredTool(
                    "wait_second",
                    "Second provider wait.",
                    {"type": "object"},
                    wait_second,
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
            ],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_first", "wait_first", {}),
                        ProviderToolCall("call_second", "wait_second", {}),
                    ),
                ),
            ],
            waiting_runtime=RecordingWaitingRuntime([], run_id="run_multiple"),
        )

        outcome = await agent.run("hello")

        assert outcome == AgentWaiting("run_multiple", first)

    asyncio.run(run())


def test_after_tool_hook_failure_wins_over_wait_request() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        order: list[str] = []
        error = RuntimeError("after hook failed")
        hooks = HookRegistry()

        def fail_after_tool(_context: HookContext) -> None:
            raise error

        hooks.register("after_tool_call", fail_after_tool, failure_policy="raise")
        agent, session, _messages, _event_bus = _agent(
            tools=[
                RegisteredTool(
                    "completed_peer",
                    "Completed peer.",
                    {"type": "object"},
                    lambda _arguments: "peer result",
                    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
                ),
                _wait_tool(reason),
            ],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_peer", "completed_peer", {}),
                        ProviderToolCall("call_wait", "request_waiting", {}),
                    ),
                ),
            ],
            waiting_runtime=RecordingWaitingRuntime(order, run_id="unused"),
            hook_manager=HookManager(hooks),
        )
        stream = await agent.run("hello", stream=True)
        events: list[object] = []

        with pytest.raises(RuntimeError) as caught:
            async for event in stream:
                events.append(event)

        assert caught.value is error
        assert order == []
        assert session.turns[0].status == "failed"
        assert not any(isinstance(event, TurnStreamWaiting) for event in events)
        assert [
            event.tool_call_id for event in events if isinstance(event, ToolStreamFailed)
        ] == ["call_peer"]

    asyncio.run(run())


def test_non_streaming_wait_projects_outcome_after_authoritative_commit() -> None:
    async def run() -> None:
        reason = WaitReason("timer", "timer_1")
        order: list[str] = []
        agent, session, _messages, _event_bus = _agent(
            tools=[_wait_tool(reason)],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "request_waiting", {}),
                    ),
                ),
            ],
            waiting_runtime=RecordingWaitingRuntime(order, run_id="run_2"),
        )

        outcome = await agent.run("hello")
        order.append("outcome_observed")

        assert outcome == AgentWaiting("run_2", reason)
        assert order == ["state_committed", "outcome_observed"]
        assert session.turns[0].status == "waiting"

    asyncio.run(run())


def test_wait_request_without_runtime_raises_without_terminal_or_tool_failure() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        agent, session, _messages, event_bus = _agent(
            tools=[_wait_tool(reason)],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "request_waiting", {}),
                    ),
                ),
            ],
        )
        stream = await agent.run("hello", stream=True)
        events: list[object] = []

        with pytest.raises(WaitingUnsupportedError):
            async for event in stream:
                events.append(event)

        assert stream.closed
        assert session.turns[0].status == "running"
        assert not any(
            isinstance(event, (ToolStreamFailed, TurnStreamWaiting, TurnStreamCompleted))
            for event in events
        )
        assert not any(isinstance(event, TurnCompletedEvent) for event in event_bus.events)

    asyncio.run(run())


def test_waiting_commit_failure_leaves_turn_running_and_emits_no_waiting() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        order: list[str] = []
        error = RuntimeError("commit failed")
        agent, session, _messages, event_bus = _agent(
            tools=[_wait_tool(reason)],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "request_waiting", {}),
                    ),
                ),
            ],
            waiting_runtime=FailingWaitingRuntime(order, error),
        )
        stream = await agent.run("hello", stream=True)
        events: list[object] = []

        with pytest.raises(RuntimeError) as caught:
            async for event in stream:
                events.append(event)

        assert caught.value is error
        assert stream.closed
        assert order == ["commit_attempted"]
        assert session.turns[0].status == "running"
        assert not any(
            isinstance(event, (ToolStreamFailed, TurnStreamWaiting, TurnStreamCompleted))
            for event in events
        )
        assert not any(isinstance(event, TurnCompletedEvent) for event in event_bus.events)

    asyncio.run(run())


def test_turn_state_supports_cancelled_terminal_status() -> None:
    turn = TurnState("turn_1", "hello")

    turn.cancel()

    assert turn.status == "cancelled"
