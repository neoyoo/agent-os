from __future__ import annotations

import sqlite3

import pytest

from agentos._waiting import WaitReason
from agentos.durable import SQLiteDurableStore
from agentos.messages import ToolCall
from agentos.runtime.errors import CheckpointCorruptedError, DurableUnsafeDataError
from agentos.runtime.run_runtime import RunRuntime
from tests.durable._fixtures import NOW, checkpoint_source, database_path


def _checkpoint(tmp_path):
    path = database_path(tmp_path)
    store = SQLiteDurableStore(path, clock=lambda: NOW)
    source = checkpoint_source()
    store.initialize_session(source.session)
    store.bind_checkpoint_source("session_1", source)
    runs = RunRuntime(session_id="session_1", store=store)
    runs.create_run(run_id="run_1")
    runs.queue("run_1")
    running = runs.start("run_1")
    return path, store, source, running.aggregate_version


def test_tool_call_arguments_are_redacted_from_sqlite_checkpoint(tmp_path) -> None:
    marker = "secret-api-key-must-not-persist"
    path, store, source, version = _checkpoint(tmp_path)
    source.messages.append_assistant(
        "calling tool",
        [ToolCall("call_1", "lookup", {"api_key": marker, "query": "value"})],
    )
    source.messages.append_tool_result("call_1", "bounded result")
    store.commit_waiting(
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        expected_version=version,
    )

    restored = store.load_checkpoint("session_1")
    assert restored is not None
    assert restored.messages[1].tool_calls[0].arguments == {}
    store.close()
    assert marker.encode() not in path.read_bytes()


def test_memory_projection_is_not_checkpointed_as_recovery_truth(tmp_path) -> None:
    marker = "stale-derived-memory-projection"
    path, store, source, version = _checkpoint(tmp_path)
    source.context.set_memory_context([marker])

    store.commit_waiting(
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        expected_version=version,
    )

    restored = store.load_checkpoint("session_1")
    assert restored is not None
    store.close()
    assert marker.encode() not in path.read_bytes()


@pytest.mark.parametrize(
    "marker",
    [
        "data:image/png;base64,QUJDREVGRw==",
        "https://example.test/file?X-Amz-Signature=secret-value",
        "C:\\private\\drawing.png",
        "Authorization: Bearer secret-value",
    ],
)
def test_checkpoint_rejects_forbidden_durable_representations(
    tmp_path,
    marker: str,
) -> None:
    path, store, source, version = _checkpoint(tmp_path)
    source.messages.append_user(marker)

    with pytest.raises(
        DurableUnsafeDataError,
        match="^durable data contains a forbidden representation$",
    ):
        store.commit_waiting(
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            reason=WaitReason("human_input", "approval_1"),
            expected_version=version,
        )

    assert store.get(session_id="session_1", run_id="run_1").status.value == "running"
    assert store.latest_checkpoint("session_1", "run_1") is None
    store.close()
    assert marker.encode() not in path.read_bytes()


@pytest.mark.parametrize(
    ("statement", "parameters", "operation"),
    [
        (
            "UPDATE durable_messages SET payload_json = replace("
            "payload_json, '\"role\":\"user\"', '\"role\":\"system\"') "
            "WHERE session_id = ?",
            ("session_1",),
            "load",
        ),
        (
            "UPDATE durable_sessions SET status = ?, next_turn_number = ? "
            "WHERE session_id = ?",
            ("invalid", 0, "session_1"),
            "load",
        ),
        (
            "UPDATE durable_runs SET aggregate_version = -1 WHERE run_id = ?",
            ("run_1",),
            "get",
        ),
        (
            "UPDATE durable_checkpoints SET created_at = ? WHERE run_id = ?",
            ("invalid-checkpoint-time", "run_1"),
            "load",
        ),
    ],
)
def test_corrupted_durable_rows_fail_with_stable_domain_error(
    tmp_path,
    statement: str,
    parameters: tuple[object, ...],
    operation: str,
) -> None:
    path, store, _source, version = _checkpoint(tmp_path)
    store.commit_waiting(
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        expected_version=version,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(statement, parameters)

    with pytest.raises(
        CheckpointCorruptedError,
        match="corrupted",
    ):
        if operation == "load":
            store.load_checkpoint("session_1")
        else:
            store.get(session_id="session_1", run_id="run_1")
    store.close()
