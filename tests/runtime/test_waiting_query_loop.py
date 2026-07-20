import asyncio
from datetime import UTC, datetime
from itertools import count

import pytest

from agentos import Agent
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolConcurrencyPolicy,
    ToolInvocation,
    ToolRegistry,
    WaitRequest,
)
from agentos.context import ContextRuntime
from agentos.events import EventBus, ToolResultAppendedEvent, TurnCompletedEvent
from agentos.hooks import HookManager, HookRegistry
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    AgentResult,
    AgentWaiting,
    ProviderRequestBuilder,
    QueryLoop,
    TurnStreamCompleted,
    TurnStreamWaiting,
    WaitReason,
)
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.session import SessionState
from agentos.runtime.side_effect_integrity import wait_reason_digest
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.side_effect_types import (
    SideEffectOutcomeKind,
    SideEffectStatus,
    WaitingToolCompletion,
)
from agentos.runtime.turn import TurnState
from agentos.runtime.waiting import WaitingCommit, WaitingRuntime
from tests._context_protocol_fixtures import default_context_renderer


_TIMER_DUE = datetime(2026, 7, 17, 12, tzinfo=UTC)


class RecordingSessionState(SessionState):
    def __init__(self) -> None:
        super().__init__("session_1")
        self.turns: list[TurnState] = []

    def new_turn(
        self,
        user_input: str,
        *,
        turn_id: str | None = None,
    ) -> TurnState:
        turn = super().new_turn(user_input, turn_id=turn_id)
        self.turns.append(turn)
        return turn


class TrapSideEffectStore(InMemorySideEffectStore):
    def __init__(self) -> None:
        super().__init__()
        self.operations: list[str] = []

    def _trap(self, operation: str) -> None:
        self.operations.append(operation)
        raise AssertionError(f"unexpected Ledger operation: {operation}")

    async def get(self, **kwargs):  # type: ignore[no-untyped-def]
        self._trap("get")

    async def reserve(self, **kwargs):  # type: ignore[no-untyped-def]
        self._trap("reserve")

    async def mark_started(self, **kwargs):  # type: ignore[no-untyped-def]
        self._trap("mark_started")

    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        self._trap("complete")

    async def mark_ambiguous(self, **kwargs):  # type: ignore[no-untyped-def]
        self._trap("mark_ambiguous")

    async def begin_compensation(self, **kwargs):  # type: ignore[no-untyped-def]
        self._trap("begin_compensation")

    async def complete_compensation(self, **kwargs):  # type: ignore[no-untyped-def]
        self._trap("complete_compensation")

    async def resolve(self, **kwargs):  # type: ignore[no-untyped-def]
        self._trap("resolve")


class FailingWaitingRunStore(InMemoryRunStore):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def transition(self, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs["status"] is RunStatus.WAITING:
            raise self.error
        return await super().transition(**kwargs)


class RecordingWaitingRuntime:
    def __init__(self, runs: RunRuntime) -> None:
        self.runs = runs
        self.completion: WaitingToolCompletion | None = None

    async def commit_waiting(
        self,
        *,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
        completion: WaitingToolCompletion | None = None,
    ) -> WaitingCommit:
        self.completion = completion
        waiting = await self.runs.wait(run_id, reason=reason, guard=guard)
        return WaitingCommit(run_id, reason, waiting.aggregate_version)


def _agent(
    *,
    tools: list[RegisteredTool],
    responses: list[ProviderResponse],
    waiting_runtime: WaitingRuntime | None = None,
    hook_manager: HookManager | None = None,
    side_effect_store: InMemorySideEffectStore | None = None,
    run_store: InMemoryRunStore | None = None,
) -> tuple[Agent, RecordingSessionState, MessageRuntime, EventBus]:
    context = ContextRuntime()
    messages = MessageRuntime()
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    session = RecordingSessionState()
    event_bus = EventBus()
    run_number = count(1)

    def next_run_id() -> str:
        return f"run_{next(run_number)}"

    run_runtime = RunRuntime(
        session_id=session.id,
        store=run_store if run_store is not None else InMemoryRunStore(),
        id_factory=next_run_id,
    )
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
                run_runtime=run_runtime,
                hook_manager=hook_manager,
                side_effect_store=side_effect_store,
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
    seen: list[ToolInvocation] | None = None,
) -> RegisteredTool:
    async def request_waiting(invocation: ToolInvocation) -> WaitRequest:
        if seen is not None:
            seen.append(invocation)
        return WaitRequest(reason)

    return RegisteredTool(
        name,
        "Request authoritative waiting.",
        {"type": "object"},
        request_waiting,
        SideEffectPolicy.PURE,
        concurrency_policy=ToolConcurrencyPolicy.EXCLUSIVE,
        wait_capable=True,
    )


def _pure_tool(name: str, calls: list[str]) -> RegisteredTool:
    def handler(invocation: ToolInvocation) -> str:
        calls.append(invocation.tool_name)
        return "unexpected"

    return RegisteredTool(
        name,
        "Pure test tool.",
        {"type": "object"},
        handler,
        SideEffectPolicy.PURE,
    )


def test_streaming_wait_commits_typed_completion_before_event() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        seen: list[ToolInvocation] = []
        store = InMemorySideEffectStore()
        agent, session, messages, event_bus = _agent(
            tools=[_wait_tool(reason, seen=seen)],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "request_waiting", {}),
                    ),
                ),
                ProviderResponse("replacement completed"),
            ],
            side_effect_store=store,
        )

        stream = await agent.run("hello", stream=True)
        events = []
        waiting_record_checked = False
        async for event in stream:
            events.append(event)
            if isinstance(event, TurnStreamWaiting):
                invocation = seen[0]
                assert agent.query_loop.run_runtime is not None
                run_state = await agent.query_loop.run_runtime.get_run("run_1")
                record = await store.get(
                    tenant_id=None,
                    session_id="session_1",
                    operation_id=invocation.context.operation_id,
                    attempt=None,
                    guard=RunWriteGuard(run_state.aggregate_version),
                )
                assert record is not None
                assert record.status is SideEffectStatus.COMPLETED
                assert record.outcome_kind is SideEffectOutcomeKind.WAIT_CONTROL
                assert record.wait_reason_digest == wait_reason_digest(reason)
                waiting_record_checked = True

        assert stream.closed
        assert waiting_record_checked
        assert session.turns[0].status == "waiting"
        assert events[-1] == TurnStreamWaiting("run_1", reason)
        assert not any(
            isinstance(event, TurnStreamCompleted)
            for event in events
        )
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

    asyncio.run(run())


@pytest.mark.parametrize(
    "tool_names",
    [
        ("request_waiting", "peer"),
        ("request_waiting", "other_wait"),
    ],
)
def test_wait_capable_batch_fails_before_hook_handler_or_ledger(
    tool_names: tuple[str, str],
) -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        handler_calls: list[str] = []
        hook_calls: list[str] = []
        hooks = HookRegistry()
        hooks.register(
            "before_tool_call",
            lambda context: hook_calls.append(context.payload["tool_call"].id),
        )
        tools = [_wait_tool(reason)]
        tools.append(
            _wait_tool(reason, name="other_wait")
            if tool_names[1] == "other_wait"
            else _pure_tool("peer", handler_calls)
        )
        store = TrapSideEffectStore()
        agent, session, messages, _event_bus = _agent(
            tools=tools,
            responses=[
                ProviderResponse(
                    tool_calls=tuple(
                        ProviderToolCall(f"call_{index}", name, {})
                        for index, name in enumerate(tool_names)
                    ),
                ),
            ],
            hook_manager=HookManager(hooks),
            side_effect_store=store,
        )

        with pytest.raises(
            ValueError,
            match="wait-capable tool batch must contain exactly one invocation",
        ):
            await agent.run("hello")

        assert hook_calls == []
        assert handler_calls == []
        assert store.operations == []
        assert session.turns[0].status == "failed"
        assert [message.role for message in messages.materialize_active()] == ["user"]

    asyncio.run(run())


def test_non_streaming_wait_projects_outcome_after_commit() -> None:
    async def run() -> None:
        reason = WaitReason("timer", "timer_1", not_before=_TIMER_DUE)
        agent, session, _messages, _event_bus = _agent(
            tools=[_wait_tool(reason)],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "request_waiting", {}),
                    ),
                ),
            ],
        )

        outcome = await agent.run("hello")

        assert outcome == AgentWaiting("run_1", reason)
        assert session.turns[0].status == "waiting"
        assert agent.query_loop.run_runtime is not None
        assert (
            (await agent.query_loop.run_runtime.get_run("run_1")).status
            is RunStatus.WAITING
        )

    asyncio.run(run())


def test_run_commit_forwards_typed_completion_to_waiting_runtime() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        runs = RunRuntime(
            session_id="session_1",
            store=InMemoryRunStore(),
            id_factory=lambda: "run_1",
        )
        run = await runs.create_run()
        run = await runs.queue("run_1", guard=RunWriteGuard(run.aggregate_version))
        run = await runs.start("run_1", guard=RunWriteGuard(run.aggregate_version))
        waiting = RecordingWaitingRuntime(runs)
        completion = WaitingToolCompletion(
            "invocation_0123456789abcdef0123456789abcdef",
            "operation_0123456789abcdef0123456789abcdef",
            1,
            wait_reason_digest(reason),
        )
        from agentos.runtime.run_commit import RunCommitRuntime

        runtime = RunCommitRuntime(runs, waiting)
        await runtime.commit_waiting(
            run_id="run_1",
            turn_id="turn_1",
            reason=reason,
            guard=RunWriteGuard(run.aggregate_version),
            completion=completion,
        )

        assert waiting.completion == completion

    asyncio.run(run())


def test_local_waiting_commit_failure_rolls_back_ledger_and_projection() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        error = RuntimeError("commit failed")
        seen: list[ToolInvocation] = []
        store = InMemorySideEffectStore()
        agent, session, messages, event_bus = _agent(
            tools=[_wait_tool(reason, seen=seen)],
            responses=[
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "request_waiting", {}),
                    ),
                ),
            ],
            side_effect_store=store,
            run_store=FailingWaitingRunStore(error),
        )
        stream = await agent.run("hello", stream=True)
        events = []

        with pytest.raises(RuntimeError, match="^commit failed$") as caught:
            async for event in stream:
                events.append(event)

        assert caught.value is error
        assert stream.closed
        assert session.turns[0].status == "running"
        assert agent.query_loop.run_runtime is not None
        assert (
            (await agent.query_loop.run_runtime.get_run("run_1")).status
            is RunStatus.RUNNING
        )
        record = await store.get(
            tenant_id=None,
            session_id="session_1",
            operation_id=seen[0].context.operation_id,
            attempt=None,
            guard=RunWriteGuard(
                (await agent.query_loop.run_runtime.get_run("run_1")).aggregate_version,
            ),
        )
        assert record is not None
        assert record.status is SideEffectStatus.STARTED
        assert record.outcome_kind is None
        assert record.wait_reason_digest is None
        assert [message.role for message in messages.materialize_active()] == [
            "user",
            "assistant",
        ]
        assert [message.role for message in messages.store.all()] == [
            "user",
            "assistant",
        ]
        assert not any(
            isinstance(event, (TurnStreamWaiting, TurnStreamCompleted))
            for event in events
        )
        assert not any(
            isinstance(event, (ToolResultAppendedEvent, TurnCompletedEvent))
            for event in event_bus.events
        )

    asyncio.run(run())


def test_turn_state_supports_cancelled_terminal_status() -> None:
    turn = TurnState("turn_1", "hello")

    turn.cancel()

    assert turn.status == "cancelled"
