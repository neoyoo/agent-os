import sqlite3
from datetime import timedelta

import pytest

from agentos._waiting import WaitReason
from agentos.durable import SQLiteDurableStore
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.durable_runtime import DurableCommandRuntime
from agentos.runtime.execution import AcceptedTurnExecution
from agentos.runtime.errors import (
    CommandConflictError,
    CommandNotDueError,
    CommandStateError,
    CheckpointCorruptedError,
    DurableUnsafeDataError,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
)
from tests.durable._async_support import create_running, run
from tests.durable._fixtures import NOW, checkpoint_source, database_path


def waiting_run(tmp_path, reason: WaitReason):
    store = run(SQLiteDurableStore.open(database_path(tmp_path), clock=lambda: NOW))
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    run(store.commit_waiting(
        checkpoint=source.capture(),
        run_id="run_1",
        turn_id="turn_1",
        reason=reason,
        guard=RunWriteGuard(running.aggregate_version),
    ))
    return store, runs


def test_exact_duplicate_command_is_not_applied_twice(tmp_path) -> None:
    store, runs = waiting_run(
        tmp_path,
        WaitReason("human_input", "approval_1"),
    )
    runtime = DurableCommandRuntime(
        session_id="session_1",
        store=store,
        clock=lambda: NOW,
    )
    command = DurableRunCommand("run_1", "cmd_1", "hitl_answer", {"answer": "yes"})

    accepted = run(runtime.accept(command))
    duplicate = run(runtime.accept(command))

    assert isinstance(accepted, AcceptedTurnExecution)
    assert isinstance(accepted.input, AcceptedContinuationInput)
    assert isinstance(duplicate, DurableCommandReceipt)
    assert duplicate.duplicate is True
    assert duplicate.aggregate_version == accepted.guard.expected_version
    assert run(runs.get_run("run_1")).status is RunStatus.QUEUED
    run(store.close())


def test_accepted_queued_continuation_can_be_recovered_after_crash(tmp_path) -> None:
    path = database_path(tmp_path)
    store, _ = waiting_run(tmp_path, WaitReason("human_input", "approval_1"))
    command = DurableRunCommand("run_1", "cmd_1", "resume", {"answer": "yes"})
    accepted = run(DurableCommandRuntime(
        "session_1",
        store,
        clock=lambda: NOW,
    ).accept(command))
    assert isinstance(accepted, AcceptedTurnExecution)
    assert isinstance(accepted.input, AcceptedContinuationInput)
    accepted_turn_id = accepted.input.turn_id
    run(store.close())

    reopened = run(SQLiteDurableStore.open(path, clock=lambda: NOW))
    recovered = run(reopened.load_pending_continuation(
        session_id="session_1",
        run_id="run_1",
    ))
    assert recovered == accepted
    assert recovered is not None
    assert recovered.input.turn_id == accepted_turn_id

    run(RunRuntime(session_id="session_1", store=reopened).start(
        "run_1",
        guard=accepted.guard,
    ))
    assert run(reopened.load_pending_continuation(
        session_id="session_1",
        run_id="run_1",
    )) is None
    run(reopened.close())


def test_unsafe_pending_command_record_uses_stable_corruption_error(tmp_path) -> None:
    path = database_path(tmp_path)
    store, _ = waiting_run(tmp_path, WaitReason("human_input", "approval_1"))
    command = DurableRunCommand("run_1", "cmd_1", "resume", {})
    run(DurableCommandRuntime(
        "session_1",
        store,
        clock=lambda: NOW,
    ).accept(command))
    run(store.close())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE durable_commands SET payload_json = ? WHERE command_id = ?",
            ('{"value":"' + ("A" * 128) + '"}', command.command_id),
        )

    reopened = run(SQLiteDurableStore.open(path, clock=lambda: NOW))
    with pytest.raises(
        CheckpointCorruptedError,
        match="^durable command record is corrupted$",
    ):
        run(reopened.load_pending_continuation(
            session_id="session_1",
            run_id="run_1",
        ))
    run(reopened.close())


def test_conflicting_duplicate_command_is_rejected(tmp_path) -> None:
    store, _ = waiting_run(tmp_path, WaitReason("human_input", "approval_1"))
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    run(runtime.accept(DurableRunCommand("run_1", "cmd_1", "resume", {})))

    with pytest.raises(CommandConflictError):
        run(runtime.accept(
            DurableRunCommand("run_1", "cmd_1", "resume", {"x": 1}),
        ))

    run(store.close())


@pytest.mark.parametrize(
    ("column", "corrupted_value"),
    (
        ("session_id", sqlite3.Binary(b"invalid-session")),
        ("run_id", sqlite3.Binary(b"invalid-run")),
        ("kind", sqlite3.Binary(b"resume")),
        ("kind", "unknown"),
        ("payload_json", sqlite3.Binary(b"{}")),
        ("payload_json", "{"),
        ("payload_json", "[]"),
        ("turn_id", None),
        ("aggregate_version", -1),
        ("aggregate_version", "invalid-version"),
    ),
)
def test_corrupted_duplicate_command_record_uses_stable_error(
    tmp_path,
    column: str,
    corrupted_value: object,
) -> None:
    path = database_path(tmp_path)
    store, _ = waiting_run(tmp_path, WaitReason("human_input", "approval_1"))
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    command = DurableRunCommand("run_1", "cmd_1", "resume", {})
    run(runtime.accept(command))
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"UPDATE durable_commands SET {column} = ? WHERE command_id = ?",
            (corrupted_value, command.command_id),
        )

    with pytest.raises(
        CheckpointCorruptedError,
        match="^durable command record is corrupted$",
    ):
        run(runtime.accept(command))

    run(store.close())


def test_cancel_command_persists_and_deduplicates(tmp_path) -> None:
    path = database_path(tmp_path)
    store, _ = waiting_run(tmp_path, WaitReason("human_input", "approval_1"))
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    command = DurableRunCommand("run_1", "cmd_cancel", "cancel", {})

    first = run(runtime.accept(command))
    duplicate = run(runtime.accept(command))
    run(store.close())
    reopened = run(SQLiteDurableStore.open(path, clock=lambda: NOW))
    state = run(reopened.get(session_id="session_1", run_id="run_1"))

    assert isinstance(first, DurableCommandReceipt)
    assert first.duplicate is False
    assert duplicate == DurableCommandReceipt(
        "run_1",
        "cmd_cancel",
        "cancel",
        first.aggregate_version,
        True,
    )
    assert state is not None and state.status is RunStatus.CANCELLED
    run(reopened.close())


def test_sqlite_runtime_fails_closed_without_resolution_resume_assembly(
    tmp_path,
) -> None:
    operation_id = "operation_d340f3861e0c6a7eefbaf707fdc69d3d"
    store, runs = waiting_run(
        tmp_path,
        WaitReason("side_effect_reconciliation", operation_id),
    )
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    command = DurableRunCommand(
        "run_1",
        "cmd_resolve",
        "resolve_side_effect",
        SideEffectResolution(operation_id, SideEffectResolutionKind.FAIL),
    )

    with pytest.raises(CommandStateError, match="not assembled"):
        run(runtime.accept(command))

    assert run(runs.get_run("run_1")).status is RunStatus.WAITING
    run(store.close())


def test_resolution_command_allows_opaque_protected_attestation(tmp_path) -> None:
    operation_id = "operation_d340f3861e0c6a7eefbaf707fdc69d3d"
    store, runs = waiting_run(
        tmp_path,
        WaitReason("side_effect_reconciliation", operation_id),
    )
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    attestation = ProtectedPayloadRef(
        "A" * 128,
        "hmac-sha256:" + "b" * 64,
    )
    command = DurableRunCommand(
        "run_1",
        "cmd_resolve",
        "resolve_side_effect",
        SideEffectResolution(
            operation_id,
            SideEffectResolutionKind.RETRY_PROVEN_SAFE,
            attestation_ref=attestation,
            attestation_digest=attestation.digest,
        ),
    )

    with pytest.raises(CommandStateError, match="not assembled"):
        run(runtime.accept(command))

    assert run(runs.get_run("run_1")).status is RunStatus.WAITING
    run(store.close())


def test_timer_command_is_rejected_until_due_without_being_recorded(tmp_path) -> None:
    due = NOW + timedelta(minutes=5)
    store, runs = waiting_run(
        tmp_path,
        WaitReason("timer", "timer_1", not_before=due),
    )
    early = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    command = DurableRunCommand("run_1", "cmd_early", "wakeup", {})

    with pytest.raises(CommandNotDueError):
        run(early.accept(command))
    assert run(runs.get_run("run_1")).status is RunStatus.WAITING

    due_runtime = DurableCommandRuntime("session_1", store, clock=lambda: due)
    accepted = run(due_runtime.accept(command))
    assert isinstance(accepted, AcceptedTurnExecution)
    assert isinstance(accepted.input, AcceptedContinuationInput)
    assert run(runs.get_run("run_1")).status is RunStatus.QUEUED
    run(store.close())


def test_terminal_and_nonwaiting_resume_are_rejected(tmp_path) -> None:
    store = run(SQLiteDurableStore.open(database_path(tmp_path), clock=lambda: NOW))
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    created = run(runs.create_run(run_id="run_1"))
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)

    with pytest.raises(CommandStateError):
        run(runtime.accept(
            DurableRunCommand("run_1", "cmd_created", "resume", {}),
        ))
    queued = run(runs.queue("run_1", guard=RunWriteGuard(created.aggregate_version)))
    running = run(runs.start("run_1", guard=RunWriteGuard(queued.aggregate_version)))
    run(runs.complete("run_1", guard=RunWriteGuard(running.aggregate_version)))
    with pytest.raises(CommandStateError):
        run(runtime.accept(
            DurableRunCommand("run_1", "cmd_terminal", "resume", {}),
        ))

    run(store.close())


def test_unsafe_command_payload_is_rejected_without_state_change(tmp_path) -> None:
    store, runs = waiting_run(
        tmp_path,
        WaitReason("human_input", "approval_1"),
    )
    command = DurableRunCommand(
        "run_1",
        "cmd_unsafe",
        "hitl_answer",
        {"provider_file_id": "file-abcdefgh12345678"},
    )

    with pytest.raises(DurableUnsafeDataError):
        run(DurableCommandRuntime(
            "session_1",
            store,
            clock=lambda: NOW,
        ).accept(command))

    assert run(runs.get_run("run_1")).status is RunStatus.WAITING
    run(store.close())
