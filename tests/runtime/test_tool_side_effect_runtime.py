import pytest

from agentos._waiting import WaitReason, WaitRequest
from agentos.capabilities import (
    SideEffectPolicy,
    ToolConcurrencyPolicy,
)
from agentos.capabilities.executor import (
    ToolExecutionError,
    ToolExecutionResult,
)
from agentos.capabilities.tools import ToolExecutionContract
from agentos.providers import ProviderToolCall
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectCompletion,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectStatus,
    SideEffectTransitionError,
)
from agentos.runtime.tool_invocations import build_tool_invocation_plan
from agentos.runtime.tool_side_effect_runtime import (
    ToolSideEffectRuntime,
    WaitingToolHandoff,
)
from tests.planning._async import async_test


def _entry():
    return build_tool_invocation_plan(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(ProviderToolCall("call_1", "lookup", {"query": "drawing"}),),
    ).entries[0]


def _contract(policy: SideEffectPolicy, *, wait_capable: bool = False):
    return ToolExecutionContract(
        policy,
        ToolConcurrencyPolicy.EXCLUSIVE,
        wait_capable,
    )


class _FailingCompletionStore(InMemorySideEffectStore):
    def __init__(self) -> None:
        super().__init__()
        self.complete_calls = 0

    async def complete(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        completion: SideEffectCompletion,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        self.complete_calls += 1
        raise SideEffectTransitionError


@async_test
async def test_completed_result_is_reused_without_running_producer() -> None:
    store = InMemorySideEffectStore()
    runtime = ToolSideEffectRuntime(store)
    guard = RunWriteGuard(0)
    calls = 0

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return ToolExecutionResult("call_1", "found")

    first = await runtime.execute(
        _entry(),
        _contract(SideEffectPolicy.PURE),
        guard=guard,
        produce=produce,
    )
    second = await runtime.execute(
        _entry(),
        _contract(SideEffectPolicy.PURE),
        guard=guard,
        produce=produce,
    )

    assert first == second == ToolExecutionResult("call_1", "found")
    assert calls == 1


@async_test
async def test_started_pure_effect_retries_with_next_attempt() -> None:
    store = InMemorySideEffectStore()
    runtime = ToolSideEffectRuntime(store)
    guard = RunWriteGuard(0)
    entry = _entry()
    reserved = await runtime.reserve(entry, _contract(SideEffectPolicy.PURE), guard)
    await store.mark_started(attempt_id=reserved.attempt_id, guard=guard)
    attempts = []

    async def produce(invocation):  # type: ignore[no-untyped-def]
        attempts.append(invocation.context.attempt)
        return ToolExecutionResult("call_1", "found")

    result = await runtime.execute(
        entry,
        _contract(SideEffectPolicy.PURE),
        guard=guard,
        produce=produce,
    )

    assert result.content == "found"
    assert attempts == [2]


@async_test
async def test_started_unsafe_effect_enters_reconciliation_without_producer() -> None:
    store = InMemorySideEffectStore()
    runtime = ToolSideEffectRuntime(store)
    guard = RunWriteGuard(0)
    entry = _entry()
    contract = _contract(SideEffectPolicy.NON_RETRYABLE)
    reserved = await runtime.reserve(entry, contract, guard)
    await store.mark_started(attempt_id=reserved.attempt_id, guard=guard)

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        raise AssertionError("ambiguous effect must not run again")

    outcome = await runtime.execute(
        entry,
        contract,
        guard=guard,
        produce=produce,
    )

    assert isinstance(outcome, WaitingToolHandoff)
    assert outcome.request.reason.kind == "side_effect_reconciliation"
    assert outcome.completion is None


@async_test
async def test_safe_handler_error_completes_with_stable_failure() -> None:
    store = InMemorySideEffectStore()
    runtime = ToolSideEffectRuntime(store)
    guard = RunWriteGuard(0)
    entry = _entry()

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret exception text")

    with pytest.raises(ToolExecutionError, match="^tool execution failed$"):
        await runtime.execute(
            entry,
            _contract(SideEffectPolicy.IDEMPOTENT),
            guard=guard,
            produce=produce,
        )

    record = await store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=entry.invocation.context.operation_id,
        attempt=None,
        guard=guard,
    )
    assert record is not None
    assert record.status is SideEffectStatus.COMPLETED
    assert record.outcome_kind is SideEffectOutcomeKind.HANDLER_ERROR
    assert record.failure_code == "tool_execution_failed"


@async_test
async def test_store_completion_failure_is_not_reclassified_as_handler_error() -> None:
    store = _FailingCompletionStore()
    runtime = ToolSideEffectRuntime(store)
    guard = RunWriteGuard(0)
    entry = _entry()

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        return ToolExecutionResult("call_1", "found")

    with pytest.raises(SideEffectTransitionError):
        await runtime.execute(
            entry,
            _contract(SideEffectPolicy.PURE),
            guard=guard,
            produce=produce,
        )

    record = await store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=entry.invocation.context.operation_id,
        attempt=None,
        guard=guard,
    )
    assert store.complete_calls == 1
    assert record is not None
    assert record.status is SideEffectStatus.STARTED


@async_test
async def test_invalid_safe_tool_result_completes_as_handler_error() -> None:
    store = InMemorySideEffectStore()
    runtime = ToolSideEffectRuntime(store)
    guard = RunWriteGuard(0)
    entry = _entry()

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        return ToolExecutionResult("another_call", "found")

    with pytest.raises(ToolExecutionError, match="^tool execution failed$"):
        await runtime.execute(
            entry,
            _contract(SideEffectPolicy.PURE),
            guard=guard,
            produce=produce,
        )

    record = await store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=entry.invocation.context.operation_id,
        attempt=None,
        guard=guard,
    )
    assert record is not None
    assert record.status is SideEffectStatus.COMPLETED
    assert record.outcome_kind is SideEffectOutcomeKind.HANDLER_ERROR


@async_test
async def test_invalid_unsafe_tool_result_enters_reconciliation() -> None:
    store = InMemorySideEffectStore()
    runtime = ToolSideEffectRuntime(store)
    guard = RunWriteGuard(0)
    entry = _entry()

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        return WaitRequest(WaitReason("human_input", "approval_1"))

    outcome = await runtime.execute(
        entry,
        _contract(SideEffectPolicy.NON_RETRYABLE),
        guard=guard,
        produce=produce,
    )

    assert isinstance(outcome, WaitingToolHandoff)
    assert outcome.request.reason.kind == "side_effect_reconciliation"
    record = await store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=entry.invocation.context.operation_id,
        attempt=None,
        guard=guard,
    )
    assert record is not None
    assert record.status is SideEffectStatus.AMBIGUOUS


@async_test
async def test_wait_handoff_leaves_ledger_started_for_composite_commit() -> None:
    store = InMemorySideEffectStore()
    runtime = ToolSideEffectRuntime(store)
    guard = RunWriteGuard(0)
    entry = _entry()

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        return WaitRequest(WaitReason("human_input", "approval_1"))

    outcome = await runtime.execute(
        entry,
        _contract(SideEffectPolicy.PURE, wait_capable=True),
        guard=guard,
        produce=produce,
    )

    assert isinstance(outcome, WaitingToolHandoff)
    assert outcome.completion is not None
    record = await store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=entry.invocation.context.operation_id,
        attempt=None,
        guard=guard,
    )
    assert record is not None
    assert record.status is SideEffectStatus.STARTED
