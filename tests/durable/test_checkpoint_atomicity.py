import sqlite3

import pytest

from agentos._waiting import WaitReason
from agentos.durable import SQLiteDurableStore
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.errors import CheckpointConflictError
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunStatus
from tests.durable._async_support import create_running, run
from tests.durable._fixtures import NOW, checkpoint_source, database_path


def test_waiting_and_checkpoint_roll_back_together(tmp_path, monkeypatch) -> None:
    store = SQLiteDurableStore(database_path(tmp_path), clock=lambda: NOW)
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    store.bind_checkpoint_source("session_1", source)
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))

    def fail_after_state_write(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("injected checkpoint failure")

    monkeypatch.setattr(store, "_write_context_state", fail_after_state_write)

    with pytest.raises(OSError, match="injected checkpoint failure"):
        run(store.commit_waiting(
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            reason=WaitReason("human_input", "approval_1"),
            guard=RunWriteGuard(running.aggregate_version),
        ))

    assert run(runs.get_run("run_1")) == running
    assert run(runs.get_run("run_1")).status is RunStatus.RUNNING
    assert run(store.latest_checkpoint("session_1", "run_1")) is None
    store.close()


def test_stale_runtime_cannot_overwrite_newer_running_continuation(tmp_path) -> None:
    path = database_path(tmp_path)
    first = SQLiteDurableStore(path, clock=lambda: NOW)
    source = checkpoint_source()
    run(first.initialize_session(source.session))
    first.bind_checkpoint_source("session_1", source)
    runs = RunRuntime(session_id="session_1", store=first)
    original_running = run(create_running(runs, "run_1"))
    run(first.commit_waiting(
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        guard=RunWriteGuard(original_running.aggregate_version),
    ))

    second = SQLiteDurableStore(path, clock=lambda: NOW)
    accepted = run(second.accept_command(
        session_id="session_1",
        command=DurableRunCommand("run_1", "cmd_1", "resume", {}),
        now=NOW,
    ))
    continued = run(
        RunRuntime(session_id="session_1", store=second).start(
            "run_1",
            guard=accepted.guard,
        ),
    )

    with pytest.raises(CheckpointConflictError, match="version"):
        run(first.commit_waiting(
            session_id="session_1",
            run_id="run_1",
            turn_id="stale_turn",
            reason=WaitReason("human_input", "stale"),
            guard=RunWriteGuard(original_running.aggregate_version),
        ))

    assert run(runs.get_run("run_1")) == continued
    first.close()
    second.close()


def test_stale_runtime_cannot_fail_newer_running_continuation(tmp_path) -> None:
    path = database_path(tmp_path)
    first = SQLiteDurableStore(path, clock=lambda: NOW)
    source = checkpoint_source()
    run(first.initialize_session(source.session))
    first.bind_checkpoint_source("session_1", source)
    original = RunRuntime(session_id="session_1", store=first)
    original_running = run(create_running(original, "run_1"))
    run(first.commit_waiting(
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        guard=RunWriteGuard(original_running.aggregate_version),
    ))

    second = SQLiteDurableStore(path, clock=lambda: NOW)
    accepted = run(second.accept_command(
        session_id="session_1",
        command=DurableRunCommand("run_1", "cmd_1", "resume", {}),
        now=NOW,
    ))
    continued = run(
        RunRuntime(session_id="session_1", store=second).start(
            "run_1",
            guard=accepted.guard,
        ),
    )

    with pytest.raises(CheckpointConflictError, match="version"):
        run(original.fail(
            "run_1",
            guard=RunWriteGuard(original_running.aggregate_version),
            turn_id="turn_1",
        ))

    assert run(original.get_run("run_1")) == continued
    first.close()
    second.close()


@pytest.mark.parametrize(
    "trigger_sql",
    [
        "CREATE TRIGGER fail_step BEFORE UPDATE ON durable_sessions "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END",
        "CREATE TRIGGER fail_step BEFORE INSERT ON durable_messages "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END",
        "CREATE TRIGGER fail_step BEFORE INSERT ON durable_active_refs "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END",
        "CREATE TRIGGER fail_step BEFORE INSERT ON durable_context_states "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END",
        "CREATE TRIGGER fail_step BEFORE INSERT ON durable_checkpoints "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END",
        "CREATE TRIGGER fail_step BEFORE UPDATE ON durable_runs "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END",
    ],
)
def test_each_waiting_persistence_step_rolls_back_atomically(
    tmp_path,
    trigger_sql: str,
) -> None:
    store = SQLiteDurableStore(database_path(tmp_path), clock=lambda: NOW)
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    store.bind_checkpoint_source("session_1", source)
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    store._connection.execute(trigger_sql)

    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        run(store.commit_waiting(
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            reason=WaitReason("human_input", "approval_1"),
            guard=RunWriteGuard(running.aggregate_version),
        ))

    assert run(runs.get_run("run_1")) == running
    assert run(store.load_checkpoint("session_1")) is None
    store.close()


def test_commit_failure_rolls_back_waiting_transaction(tmp_path) -> None:
    store = SQLiteDurableStore(database_path(tmp_path), clock=lambda: NOW)
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    store.bind_checkpoint_source("session_1", source)
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    connection = store._connection

    class FailingCommitConnection:
        def __init__(self) -> None:
            self.failed = False

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(connection, name)

        def commit(self) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("commit failed")
            connection.commit()

    store._connection = FailingCommitConnection()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="commit failed"):
        run(store.commit_waiting(
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            reason=WaitReason("human_input", "approval_1"),
            guard=RunWriteGuard(running.aggregate_version),
        ))

    assert run(runs.get_run("run_1")) == running
    assert run(store.load_checkpoint("session_1")) is None
    store.close()
