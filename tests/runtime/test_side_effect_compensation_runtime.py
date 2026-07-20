import pytest

from agentos.capabilities import (
    SideEffectPolicy,
    ToolInvocation,
    ToolInvocationContext,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_compensation import ToolCompensationRuntime
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectStatus,
)
from agentos.runtime.tool_identity import invocation_digest
from tests.planning._async import async_test


_INVOCATION_ID = "invocation_ea91f27fdf596bceb7c90d2578c5e988"
_OPERATION_ID = "operation_d340f3861e0c6a7eefbaf707fdc69d3d"


def _invocation() -> ToolInvocation:
    return ToolInvocation(
        "charge",
        {"amount": 50},
        ToolInvocationContext(
            invocation_id=_INVOCATION_ID,
            operation_id=_OPERATION_ID,
            tenant_id=None,
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            tool_call_id="call_1",
            attempt=1,
        ),
    )


async def _compensating():  # type: ignore[no-untyped-def]
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(0)
    invocation = _invocation()
    record = await store.reserve(
        invocation=invocation,
        policy=SideEffectPolicy.COMPENSATABLE,
        invocation_digest=invocation_digest(invocation),
        invocation_ref=None,
        guard=guard,
    )
    record = await store.mark_started(attempt_id=record.attempt_id, guard=guard)
    record = await store.mark_ambiguous(attempt_id=record.attempt_id, guard=guard)
    record = await store.resolve(
        attempt_id=record.attempt_id,
        resolution=SideEffectResolution(
            _OPERATION_ID,
            SideEffectResolutionKind.COMPENSATE,
        ),
        guard=guard,
    )
    return store, guard, invocation, record


@async_test
async def test_compensation_runtime_passes_only_typed_frozen_invocation() -> None:
    store, guard, invocation, record = await _compensating()
    received = []

    async def invoke(compensation):  # type: ignore[no-untyped-def]
        received.append(compensation)

    completed = await ToolCompensationRuntime(store).execute(
        record=record,
        invocation=invocation,
        guard=guard,
        invoke=invoke,
    )

    assert completed.status is SideEffectStatus.COMPENSATED
    assert received[0].arguments == {"amount": 50}
    assert received[0].context.original_operation_id == _OPERATION_ID
    assert (
        received[0].context.compensation_operation_id
        == record.compensation_operation_id
    )
    assert received[0].result_ref is None


@async_test
async def test_compensation_failure_leaves_compensating_for_stable_id_retry() -> None:
    store, guard, invocation, record = await _compensating()
    operation_ids = []

    async def fail(compensation):  # type: ignore[no-untyped-def]
        operation_ids.append(compensation.context.compensation_operation_id)
        raise RuntimeError("external outcome unknown")

    runtime = ToolCompensationRuntime(store)
    with pytest.raises(RuntimeError, match="external outcome unknown"):
        await runtime.execute(
            record=record,
            invocation=invocation,
            guard=guard,
            invoke=fail,
        )

    current = await store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=_OPERATION_ID,
        attempt=None,
        guard=guard,
    )
    assert current is not None
    assert current.status is SideEffectStatus.COMPENSATING

    async def succeed(compensation):  # type: ignore[no-untyped-def]
        operation_ids.append(compensation.context.compensation_operation_id)

    completed = await runtime.execute(
        record=current,
        invocation=invocation,
        guard=guard,
        invoke=succeed,
        recovery=True,
    )

    assert completed.status is SideEffectStatus.COMPENSATED
    assert completed.compensation_attempt == 2
    assert operation_ids == [record.compensation_operation_id] * 2


@async_test
async def test_compensated_record_never_invokes_handler_again() -> None:
    store, guard, invocation, record = await _compensating()
    calls = 0

    async def invoke(_compensation):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1

    runtime = ToolCompensationRuntime(store)
    completed = await runtime.execute(
        record=record,
        invocation=invocation,
        guard=guard,
        invoke=invoke,
    )
    replayed = await runtime.execute(
        record=completed,
        invocation=invocation,
        guard=guard,
        invoke=invoke,
        recovery=True,
    )

    assert replayed == completed
    assert calls == 1
