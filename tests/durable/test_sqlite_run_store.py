import asyncio
import gc
import sqlite3
import weakref

import pytest

from agentos._waiting import WaitReason
from agentos.durable import SQLiteDurableStore
from agentos.runtime.durable_runtime import DurableWaitingRuntime
from agentos.runtime.errors import CheckpointConflictError, CheckpointCorruptedError
from agentos.runtime.run_runtime import RunRuntime
from agentos.runtime.run_state import RunStatus
from tests.durable._fixtures import NOW, checkpoint_source, database_path


def test_wait_checkpoint_survives_store_restart(tmp_path) -> None:
    path = database_path(tmp_path)
    store = SQLiteDurableStore(path, clock=lambda: NOW)
    source = checkpoint_source()
    store.initialize_session(source.session)
    store.bind_checkpoint_source("session_1", source)
    runs = RunRuntime(session_id="session_1", store=store)
    runs.create_run(run_id="run_1")
    runs.queue("run_1")
    running = runs.start("run_1")

    commit = asyncio.run(
        DurableWaitingRuntime(
            session_id="session_1",
            store=store,
        ).commit_waiting(
            run_id="run_1",
            turn_id="turn_1",
            reason=WaitReason("human_input", "approval_1"),
            expected_version=running.aggregate_version,
        )
    )
    version = runs.get_run("run_1").aggregate_version
    store.close()

    reopened = SQLiteDurableStore(path, clock=lambda: NOW)
    restored = reopened.load_checkpoint("session_1")
    run = reopened.get(session_id="session_1", run_id="run_1")

    assert commit.run_id == "run_1"
    assert run is not None
    assert run.status is RunStatus.WAITING
    assert run.wait_reason == WaitReason("human_input", "approval_1")
    assert run.aggregate_version == version
    assert restored is not None
    assert restored.messages[0].content == "question"
    assert restored.context.working_state == {
        "task_goal": "resume after restart",
    }
    reopened.close()


def test_live_checkpoint_source_cannot_be_replaced(tmp_path) -> None:
    store = SQLiteDurableStore(database_path(tmp_path), clock=lambda: NOW)
    source = checkpoint_source()
    store.bind_checkpoint_source("session_1", source)

    with pytest.raises(CheckpointConflictError, match="already bound"):
        store.bind_checkpoint_source("session_1", checkpoint_source())

    store.close()


def test_collected_checkpoint_source_can_be_rebound(tmp_path) -> None:
    store = SQLiteDurableStore(database_path(tmp_path), clock=lambda: NOW)
    source = checkpoint_source()
    source_ref = weakref.ref(source)
    store.bind_checkpoint_source("session_1", source)

    del source
    gc.collect()

    assert source_ref() is None
    replacement = checkpoint_source()
    store.bind_checkpoint_source("session_1", replacement)
    store.close()


def test_abandoned_running_run_fails_closed_without_execution(tmp_path) -> None:
    path = database_path(tmp_path)
    store = SQLiteDurableStore(path, clock=lambda: NOW)
    source = checkpoint_source()
    store.initialize_session(source.session)
    runs = RunRuntime(session_id="session_1", store=store)
    runs.create_run(run_id="run_1")
    runs.queue("run_1")
    running = runs.start("run_1")
    store.close()

    reopened = SQLiteDurableStore(path, clock=lambda: NOW)
    recovered = reopened.recover_abandoned_runs("session_1")
    state = reopened.get(session_id="session_1", run_id="run_1")

    assert [item.run_id for item in recovered] == ["run_1"]
    assert state is not None
    assert state.status is RunStatus.FAILED
    assert state.aggregate_version == running.aggregate_version + 1
    assert reopened.recover_abandoned_runs("session_1") == ()
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
