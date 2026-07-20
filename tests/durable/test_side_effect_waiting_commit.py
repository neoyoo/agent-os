import sqlite3

import pytest

from agentos._waiting import WaitReason
from agentos.capabilities import SideEffectPolicy
from agentos.durable import SQLiteDurableStore
from agentos.providers import ProviderToolCall
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_integrity import wait_reason_digest
from agentos.runtime.side_effect_types import (
    SideEffectStatus,
    WaitingToolCompletion,
)
from agentos.runtime.tool_identity import invocation_digest
from agentos.runtime.tool_invocations import build_tool_invocation_plan
from tests.durable._async_support import create_running, run
from tests.durable._fixtures import NOW, checkpoint_source, database_path


@pytest.fixture
def open_store():
    stores: list[SQLiteDurableStore] = []

    def open_path(path):  # type: ignore[no-untyped-def]
        store = run(SQLiteDurableStore.open(path, clock=lambda: NOW))
        stores.append(store)
        return store

    yield open_path
    for store in reversed(stores):
        run(store.close())


def _invocation():
    return build_tool_invocation_plan(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(ProviderToolCall("call_1", "request_waiting", {}),),
    ).entries[0].invocation


def _started_wait(
    store: SQLiteDurableStore,
    guard: RunWriteGuard,
):
    invocation = _invocation()
    record = run(store.side_effect_store.reserve(
        invocation=invocation,
        policy=SideEffectPolicy.PURE,
        invocation_digest=invocation_digest(invocation),
        invocation_ref=ProtectedPayloadRef(
            "sealed-invocation",
            f"sha256:{'1' * 64}",
        ),
        guard=guard,
    ))
    return invocation, run(store.side_effect_store.mark_started(
        attempt_id=record.attempt_id,
        guard=guard,
    ))


def test_wait_control_ledger_and_checkpoint_survive_restart(
    tmp_path,
    open_store,
) -> None:
    path = database_path(tmp_path)
    store = open_store(path)
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    guard = RunWriteGuard(running.aggregate_version)
    invocation, started = _started_wait(store, guard)
    reason = WaitReason("human_input", "approval_1")
    completion = WaitingToolCompletion(
        invocation.context.invocation_id,
        invocation.context.operation_id,
        1,
        wait_reason_digest(reason),
    )

    commit = run(store.commit_waiting(
        checkpoint=source.capture(),
        run_id="run_1",
        turn_id="turn_1",
        reason=reason,
        guard=guard,
        completion=completion,
    ))

    assert started.status is SideEffectStatus.STARTED
    assert run(runs.get_run("run_1")).status is RunStatus.WAITING
    run(store.close())

    reopened = open_store(path)
    record = run(reopened.side_effect_store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=invocation.context.operation_id,
        attempt=None,
        guard=RunWriteGuard(commit.aggregate_version),
    ))

    assert record is not None
    assert record.status is SideEffectStatus.COMPLETED
    assert record.wait_reason_digest == wait_reason_digest(reason)
    run(reopened.close())


def test_wait_control_rolls_back_with_failed_checkpoint(
    tmp_path,
    open_store,
) -> None:
    store = open_store(database_path(tmp_path))
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    guard = RunWriteGuard(running.aggregate_version)
    invocation, started = _started_wait(store, guard)
    reason = WaitReason("human_input", "approval_1")
    completion = WaitingToolCompletion(
        invocation.context.invocation_id,
        invocation.context.operation_id,
        1,
        wait_reason_digest(reason),
    )
    run(store._connection.execute(
        "CREATE TRIGGER fail_wait_run BEFORE UPDATE ON durable_runs "
        "BEGIN SELECT RAISE(ABORT, 'injected waiting failure'); END",
    ))

    with pytest.raises(sqlite3.IntegrityError, match="injected waiting failure"):
        run(store.commit_waiting(
            checkpoint=source.capture(),
            run_id="run_1",
            turn_id="turn_1",
            reason=reason,
            guard=guard,
            completion=completion,
        ))

    record = run(store.side_effect_store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=invocation.context.operation_id,
        attempt=None,
        guard=guard,
    ))
    assert record == started
    assert run(runs.get_run("run_1")) == running
    assert run(store.latest_checkpoint("session_1", "run_1")) is None
    run(store.close())
