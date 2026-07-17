from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import cast

from agentos._json_values import thaw_json_value
from agentos.durable.serialization import dump_json, load_json_object
from agentos.durable.sqlite_records import require_run, update_run
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.errors import (
    CommandConflictError,
    CommandNotDueError,
    CommandStateError,
    CheckpointCorruptedError,
    DurableUnsafeDataError,
)
from agentos.runtime.run_state import RunState, RunStatus


def accept_command(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    command: DurableRunCommand,
    now: datetime,
) -> AcceptedContinuationInput | DurableCommandReceipt:
    payload_json = dump_json(thaw_json_value(command.payload))
    duplicate = connection.execute(
        "SELECT * FROM durable_commands WHERE command_id = ?",
        (command.command_id,),
    ).fetchone()
    if duplicate is not None:
        return _duplicate_receipt(
            duplicate,
            session_id=session_id,
            command=command,
            payload_json=payload_json,
        )
    current = require_run(connection, session_id, command.run_id)
    if command.kind == "cancel":
        _require_non_terminal(current)
        updated = current.transition(RunStatus.CANCELLED)
    else:
        _validate_continuation(command, current, now)
        updated = current.transition(RunStatus.QUEUED)
    connection.execute(
        "INSERT INTO durable_commands "
        "(command_id, session_id, run_id, kind, payload_json, aggregate_version) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            command.command_id,
            session_id,
            command.run_id,
            command.kind,
            payload_json,
            updated.aggregate_version,
        ),
    )
    update_run(connection, updated)
    if command.kind == "cancel":
        return DurableCommandReceipt(
            command.run_id,
            command.command_id,
            command.kind,
            updated.aggregate_version,
            False,
        )
    return AcceptedContinuationInput(
        command.run_id,
        command.command_id,
        command.kind,
        command.payload,
        updated.aggregate_version,
    )


def load_pending_continuation(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    run_id: str,
) -> AcceptedContinuationInput | None:
    """读取已接受但尚未开始执行的 QUEUED continuation。"""

    row = connection.execute(
        """
        SELECT command_id, run_id, kind, payload_json, aggregate_version
        FROM durable_commands
        WHERE session_id = ? AND run_id = ? AND kind != 'cancel'
          AND aggregate_version = (
              SELECT aggregate_version FROM durable_runs
              WHERE session_id = ? AND run_id = ? AND status = 'queued'
          )
        ORDER BY rowid DESC LIMIT 1
        """,
        (session_id, run_id, session_id, run_id),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = row["payload_json"]
        if not isinstance(payload, str):
            raise TypeError("payload_json")
        return AcceptedContinuationInput(
            run_id=row["run_id"],
            command_id=row["command_id"],
            kind=cast(object, row["kind"]),  # type: ignore[arg-type]
            payload=load_json_object(payload),
            aggregate_version=row["aggregate_version"],
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError(
            "durable command record is corrupted",
        ) from None


def _validate_continuation(
    command: DurableRunCommand,
    state: RunState,
    now: datetime,
) -> None:
    if state.status is not RunStatus.WAITING or state.wait_reason is None:
        raise CommandStateError("continuation command requires a waiting run")
    allowed = {
        "hitl_answer": {"human_input"},
        "resume": {"human_input", "remote_result", "resource_availability"},
        "wakeup": {"timer", "remote_result", "resource_availability"},
        "retry": {"retry_backoff"},
    }
    if state.wait_reason.kind not in allowed[command.kind]:
        raise CommandStateError("command is incompatible with wait reason")
    due = state.wait_reason.not_before
    if due is not None and _utc(now) < due:
        raise CommandNotDueError("durable command is not due")


def _duplicate_receipt(
    row: sqlite3.Row,
    *,
    session_id: str,
    command: DurableRunCommand,
    payload_json: str,
) -> DurableCommandReceipt:
    try:
        stored_session_id = row["session_id"]
        stored_run_id = row["run_id"]
        stored_command_id = row["command_id"]
        stored_kind = row["kind"]
        stored_payload_json = row["payload_json"]
        stored_version = row["aggregate_version"]
        if type(stored_session_id) is not str or not stored_session_id.strip():
            raise TypeError("session_id")
        if type(stored_payload_json) is not str:
            raise TypeError("payload_json")
        stored_payload = load_json_object(stored_payload_json)
        if dump_json(stored_payload) != stored_payload_json:
            raise ValueError("payload_json")
        receipt = DurableCommandReceipt(
            run_id=stored_run_id,
            command_id=stored_command_id,
            kind=stored_kind,
            aggregate_version=stored_version,
            duplicate=True,
        )
    except (
        CheckpointCorruptedError,
        DurableUnsafeDataError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise CheckpointCorruptedError(
            "durable command record is corrupted",
        ) from None
    if (
        stored_session_id != session_id
        or receipt.run_id != command.run_id
        or receipt.command_id != command.command_id
        or receipt.kind != command.kind
        or stored_payload_json != payload_json
    ):
        raise CommandConflictError("command_id already identifies another command")
    return receipt


def _require_non_terminal(state: RunState) -> None:
    if state.status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        raise CommandStateError("terminal run rejects durable command")


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("durable clock must return timezone-aware datetime")
    return value.astimezone(UTC)
