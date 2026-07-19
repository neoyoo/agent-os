import sqlite3

import pytest

from agentos._waiting import WaitReason
from agentos.durable import SQLiteDurableStore
from agentos.runtime.errors import CheckpointCorruptedError
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunStatus
from tests.durable._async_support import create_running, run
from tests.durable._fixtures import NOW, checkpoint_source, database_path


def test_wait_checkpoint_survives_store_restart(tmp_path) -> None:
    path = database_path(tmp_path)
    store = SQLiteDurableStore(path, clock=lambda: NOW)
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))

    commit = run(store.commit_waiting(
        checkpoint=source.capture(),
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        guard=RunWriteGuard(running.aggregate_version),
    ))
    version = run(runs.get_run("run_1")).aggregate_version
    store.close()

    reopened = SQLiteDurableStore(path, clock=lambda: NOW)
    restored = run(reopened.load_checkpoint("session_1"))
    stored_run = run(reopened.get(session_id="session_1", run_id="run_1"))

    assert commit.run_id == "run_1"
    assert stored_run is not None
    assert stored_run.status is RunStatus.WAITING
    assert stored_run.wait_reason == WaitReason("human_input", "approval_1")
    assert stored_run.aggregate_version == version
    assert restored is not None
    assert restored.messages[0].content == "question"
    assert restored.context.working_state == {
        "task_goal": "resume after restart",
    }
    reopened.close()


def test_abandoned_running_run_fails_closed_without_execution(tmp_path) -> None:
    path = database_path(tmp_path)
    store = SQLiteDurableStore(path, clock=lambda: NOW)
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    committed = run(store.commit_running(
        checkpoint=source.capture(
            execution_cursor=RunExecutionCursor(
                "turn_1",
                "before_provider",
                0,
            ),
        ),
        run_id="run_1",
        turn_id="turn_1",
        guard=RunWriteGuard(running.aggregate_version),
    ))
    store.close()

    reopened = SQLiteDurableStore(path, clock=lambda: NOW)
    recovered = run(reopened.recover_abandoned_runs("session_1"))
    state = run(reopened.get(session_id="session_1", run_id="run_1"))

    assert [item.run_id for item in recovered] == ["run_1"]
    assert state is not None
    assert state.status is RunStatus.FAILED
    assert state.aggregate_version == committed.aggregate_version + 1
    checkpoint = run(reopened.load_checkpoint("session_1"))
    assert checkpoint is not None
    assert checkpoint.execution_cursor is None
    assert run(reopened.recover_abandoned_runs("session_1")) == ()
    reopened.close()


def test_store_rejects_malformed_schema_at_open(tmp_path) -> None:
    path = database_path(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE durable_schema (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO durable_schema VALUES (1)")
        connection.execute("CREATE TABLE durable_sessions (session_id TEXT)")

    with pytest.raises(
        CheckpointCorruptedError,
        match="^durable schema is corrupted$",
    ):
        SQLiteDurableStore(path)
