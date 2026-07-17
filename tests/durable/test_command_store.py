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
from agentos.runtime.errors import (
    CommandConflictError,
    CommandNotDueError,
    CommandStateError,
    CheckpointCorruptedError,
    DurableUnsafeDataError,
)
from agentos.runtime.run_runtime import RunRuntime
from agentos.runtime.run_state import RunStatus
from tests.durable._fixtures import NOW, checkpoint_source, database_path


def waiting_run(tmp_path, reason: WaitReason):
    store = SQLiteDurableStore(database_path(tmp_path), clock=lambda: NOW)
    source = checkpoint_source()
    store.initialize_session(source.session)
    store.bind_checkpoint_source("session_1", source)
    runs = RunRuntime(session_id="session_1", store=store)
    runs.create_run(run_id="run_1")
    runs.queue("run_1")
    running = runs.start("run_1")
    store.commit_waiting(
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        reason=reason,
        expected_version=running.aggregate_version,
    )
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

    accepted = runtime.accept(command)
    duplicate = runtime.accept(command)

    assert isinstance(accepted, AcceptedContinuationInput)
    assert isinstance(duplicate, DurableCommandReceipt)
    assert duplicate.duplicate is True
    assert duplicate.aggregate_version == accepted.aggregate_version
    assert runs.get_run("run_1").status is RunStatus.QUEUED
    store.close()


def test_accepted_queued_continuation_can_be_recovered_after_crash(tmp_path) -> None:
    path = database_path(tmp_path)
    store, _ = waiting_run(tmp_path, WaitReason("human_input", "approval_1"))
    command = DurableRunCommand("run_1", "cmd_1", "resume", {"answer": "yes"})
    accepted = DurableCommandRuntime(
        "session_1",
        store,
        clock=lambda: NOW,
    ).accept(command)
    assert isinstance(accepted, AcceptedContinuationInput)
    store.close()

    reopened = SQLiteDurableStore(path, clock=lambda: NOW)
    assert reopened.load_pending_continuation(
        session_id="session_1",
        run_id="run_1",
    ) == accepted

    RunRuntime(session_id="session_1", store=reopened).start("run_1")
    assert reopened.load_pending_continuation(
        session_id="session_1",
        run_id="run_1",
    ) is None
    reopened.close()


def test_conflicting_duplicate_command_is_rejected(tmp_path) -> None:
    store, _ = waiting_run(tmp_path, WaitReason("human_input", "approval_1"))
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    runtime.accept(DurableRunCommand("run_1", "cmd_1", "resume", {}))

    with pytest.raises(CommandConflictError):
        runtime.accept(DurableRunCommand("run_1", "cmd_1", "resume", {"x": 1}))

    store.close()


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
    runtime.accept(command)
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"UPDATE durable_commands SET {column} = ? WHERE command_id = ?",
            (corrupted_value, command.command_id),
        )

    with pytest.raises(
        CheckpointCorruptedError,
        match="^durable command record is corrupted$",
    ):
        runtime.accept(command)

    store.close()


def test_cancel_command_persists_and_deduplicates(tmp_path) -> None:
    path = database_path(tmp_path)
    store, _ = waiting_run(tmp_path, WaitReason("human_input", "approval_1"))
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    command = DurableRunCommand("run_1", "cmd_cancel", "cancel", {})

    first = runtime.accept(command)
    duplicate = runtime.accept(command)
    store.close()
    reopened = SQLiteDurableStore(path, clock=lambda: NOW)
    state = reopened.get(session_id="session_1", run_id="run_1")

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
    reopened.close()


def test_timer_command_is_rejected_until_due_without_being_recorded(tmp_path) -> None:
    due = NOW + timedelta(minutes=5)
    store, runs = waiting_run(
        tmp_path,
        WaitReason("timer", "timer_1", not_before=due),
    )
    early = DurableCommandRuntime("session_1", store, clock=lambda: NOW)
    command = DurableRunCommand("run_1", "cmd_early", "wakeup", {})

    with pytest.raises(CommandNotDueError):
        early.accept(command)
    assert runs.get_run("run_1").status is RunStatus.WAITING

    due_runtime = DurableCommandRuntime("session_1", store, clock=lambda: due)
    accepted = due_runtime.accept(command)
    assert isinstance(accepted, AcceptedContinuationInput)
    assert runs.get_run("run_1").status is RunStatus.QUEUED
    store.close()


def test_terminal_and_nonwaiting_resume_are_rejected(tmp_path) -> None:
    store = SQLiteDurableStore(database_path(tmp_path), clock=lambda: NOW)
    source = checkpoint_source()
    store.initialize_session(source.session)
    runs = RunRuntime(session_id="session_1", store=store)
    runs.create_run(run_id="run_1")
    runtime = DurableCommandRuntime("session_1", store, clock=lambda: NOW)

    with pytest.raises(CommandStateError):
        runtime.accept(DurableRunCommand("run_1", "cmd_created", "resume", {}))
    runs.queue("run_1")
    runs.start("run_1")
    runs.complete("run_1")
    with pytest.raises(CommandStateError):
        runtime.accept(DurableRunCommand("run_1", "cmd_terminal", "resume", {}))

    store.close()


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
        DurableCommandRuntime("session_1", store, clock=lambda: NOW).accept(command)

    assert runs.get_run("run_1").status is RunStatus.WAITING
    store.close()
