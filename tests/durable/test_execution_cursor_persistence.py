from agentos.durable import SQLiteDurableStore
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunStatus
from tests.durable._async_support import create_running, run
from tests.durable._fixtures import NOW, checkpoint_source, database_path


def test_running_cursor_survives_store_restart_and_advances_guard(tmp_path) -> None:
    path = database_path(tmp_path)
    store = run(SQLiteDurableStore.open(path, clock=lambda: NOW))
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    cursor = RunExecutionCursor("turn_1", "before_provider", 0)

    committed = run(store.commit_running(
        checkpoint=source.capture(execution_cursor=cursor),
        run_id="run_1",
        turn_id="turn_1",
        guard=RunWriteGuard(running.aggregate_version),
    ))
    run(store.close())

    reopened = run(SQLiteDurableStore.open(path, clock=lambda: NOW))
    restored = run(reopened.load_checkpoint("session_1"))
    state = run(reopened.get(session_id="session_1", run_id="run_1"))

    assert restored is not None
    assert restored.execution_cursor == cursor
    assert state is not None
    assert state.status is RunStatus.RUNNING
    assert state.aggregate_version == committed.aggregate_version
    assert committed.aggregate_version == running.aggregate_version + 1
    run(reopened.close())


def test_terminal_checkpoint_clears_running_cursor(tmp_path) -> None:
    store = run(SQLiteDurableStore.open(database_path(tmp_path), clock=lambda: NOW))
    source: RuntimeCheckpointSource = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    first = run(store.commit_running(
        checkpoint=source.capture(
            execution_cursor=RunExecutionCursor("turn_1", "before_provider", 0),
        ),
        run_id="run_1",
        turn_id="turn_1",
        guard=RunWriteGuard(running.aggregate_version),
    ))

    run(store.commit_terminal(
        checkpoint=source.capture(),
        run_id="run_1",
        turn_id="turn_1",
        status="completed",
        guard=RunWriteGuard(first.aggregate_version),
    ))
    restored = run(store.load_checkpoint("session_1"))

    assert restored is not None
    assert restored.execution_cursor is None
    run(store.close())
