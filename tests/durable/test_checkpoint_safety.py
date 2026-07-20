from __future__ import annotations

import sqlite3

import pytest

from agentos._waiting import WaitReason
from agentos._json_values import freeze_json_mapping
from agentos.durable import SQLiteDurableStore
from agentos.messages import ToolCall
from agentos.providers import ProviderToolCall
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.errors import CheckpointCorruptedError, DurableUnsafeDataError
from agentos.runtime.payloads import PayloadProtectionContext
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.security import FernetPayloadProtector
from tests.durable._async_support import create_running, run
from tests.durable._fixtures import NOW, checkpoint_source, database_path


def _checkpoint(tmp_path):
    path = database_path(tmp_path)
    store = run(SQLiteDurableStore.open(path, clock=lambda: NOW))
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    return path, store, source, running.aggregate_version


def test_tool_call_arguments_are_redacted_from_sqlite_checkpoint(tmp_path) -> None:
    marker = "secret-api-key-must-not-persist"
    path, store, source, version = _checkpoint(tmp_path)
    protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
    payloads = ToolPayloadRuntime(
        protector=protector,
        context=PayloadProtectionContext(None, "session_1"),
    )
    source = RuntimeCheckpointSource(
        source.session,
        source.messages,
        source.context,
        payloads=payloads,
    )
    calls = (
        ProviderToolCall(
            "call_1",
            "lookup",
            freeze_json_mapping({"api_key": marker, "query": "value"}),
        ),
    )
    assistant = source.messages.append_assistant(
        "calling tool",
        [ToolCall(call.id, call.name, call.arguments) for call in calls],
    )
    plan = payloads.build_plan(
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id=assistant.id,
        calls=calls,
    )
    payloads.pending_cursor(plan)
    source.messages.append_tool_result("call_1", "bounded result")
    run(store.commit_waiting(
        checkpoint=source.capture(),
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        guard=RunWriteGuard(version),
    ))

    restored = run(store.load_checkpoint("session_1"))
    assert restored is not None
    hydrated = ToolPayloadRuntime(
        protector=protector,
        context=PayloadProtectionContext(None, "session_1"),
    ).restore_messages(restored.messages)
    assert hydrated[1].tool_calls[0].arguments == calls[0].arguments
    run(store.close())
    assert marker.encode() not in path.read_bytes()


def test_memory_projection_is_not_checkpointed_as_recovery_truth(tmp_path) -> None:
    marker = "stale-derived-memory-projection"
    path, store, source, version = _checkpoint(tmp_path)
    source.context.set_memory_context([marker])

    run(store.commit_waiting(
        checkpoint=source.capture(),
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        guard=RunWriteGuard(version),
    ))

    restored = run(store.load_checkpoint("session_1"))
    assert restored is not None
    run(store.close())
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
        run(store.commit_waiting(
            checkpoint=source.capture(),
            run_id="run_1",
            turn_id="turn_1",
            reason=WaitReason("human_input", "approval_1"),
            guard=RunWriteGuard(version),
        ))

    stored_run = run(store.get(session_id="session_1", run_id="run_1"))
    assert stored_run is not None
    assert stored_run.status.value == "running"
    assert run(store.latest_checkpoint("session_1", "run_1")) is None
    run(store.close())
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
    run(store.commit_waiting(
        checkpoint=_source.capture(),
        run_id="run_1",
        turn_id="turn_1",
        reason=WaitReason("human_input", "approval_1"),
        guard=RunWriteGuard(version),
    ))
    with sqlite3.connect(path) as connection:
        connection.execute(statement, parameters)

    with pytest.raises(
        CheckpointCorruptedError,
        match="corrupted",
    ):
        if operation == "load":
            run(store.load_checkpoint("session_1"))
        else:
            run(store.get(session_id="session_1", run_id="run_1"))
    run(store.close())
