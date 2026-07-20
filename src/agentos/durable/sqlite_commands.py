from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import aiosqlite

from agentos._json_values import thaw_json_value
from agentos.durable.serialization import dump_json, load_json_object
from agentos.durable.sqlite_records import require_run, update_run
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.execution import AcceptedTurnExecution
from agentos.runtime.errors import (
    CommandConflictError,
    CommandNotDueError,
    CommandStateError,
    CheckpointCorruptedError,
    DurableUnsafeDataError,
)
from agentos.runtime.run_state import RunState, RunStatus
from agentos.runtime.run_runtime import RunWriteGuard


async def accept_command(
    connection: aiosqlite.Connection,
    *,
    session_id: str,
    command: DurableRunCommand,
    now: datetime,
) -> AcceptedTurnExecution | DurableCommandReceipt:
    payload_json = dump_json(thaw_json_value(command.payload))
    async with connection.execute(
        "SELECT * FROM durable_commands WHERE command_id = ?",
        (command.command_id,),
    ) as cursor:
        duplicate = await cursor.fetchone()
    if duplicate is not None:
        return _duplicate_receipt(
            duplicate,
            session_id=session_id,
            command=command,
            payload_json=payload_json,
        )
    current = await require_run(connection, session_id, command.run_id)
    if command.kind == "cancel":
        _require_non_terminal(current)
        updated = current.transition(RunStatus.CANCELLED)
        turn_id = None
    else:
        _validate_continuation(command, current, now)
        updated = current.transition(RunStatus.QUEUED)
        turn_id = await _allocate_turn_id(connection, session_id)
    await connection.execute(
        "INSERT INTO durable_commands "
        "(command_id, session_id, run_id, kind, payload_json, turn_id, "
        "aggregate_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            command.command_id,
            session_id,
            command.run_id,
            command.kind,
            payload_json,
            turn_id,
            updated.aggregate_version,
        ),
    )
    await update_run(connection, updated)
    if command.kind == "cancel":
        return DurableCommandReceipt(
            command.run_id,
            command.command_id,
            command.kind,
            updated.aggregate_version,
            False,
        )
    assert turn_id is not None
    return AcceptedTurnExecution(
        input=AcceptedContinuationInput(
            command.run_id,
            command.command_id,
            command.kind,
            command.payload,
            turn_id,
        ),
        guard=RunWriteGuard(updated.aggregate_version),
        mode="start",
    )


async def load_pending_continuation(
    connection: aiosqlite.Connection,
    *,
    session_id: str,
    run_id: str,
) -> AcceptedTurnExecution | None:
    """读取已接受但尚未开始执行的 QUEUED continuation。"""

    async with connection.execute(
        """
        SELECT command_id, run_id, kind, payload_json, turn_id, aggregate_version
        FROM durable_commands
        WHERE session_id = ? AND run_id = ? AND kind != 'cancel'
          AND aggregate_version = (
              SELECT aggregate_version FROM durable_runs
              WHERE session_id = ? AND run_id = ? AND status = 'queued'
          )
        ORDER BY rowid DESC LIMIT 1
        """,
        (session_id, run_id, session_id, run_id),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    try:
        payload = row["payload_json"]
        if not isinstance(payload, str):
            raise TypeError("payload_json")
        return AcceptedTurnExecution(
            input=AcceptedContinuationInput(
                run_id=row["run_id"],
                command_id=row["command_id"],
                kind=cast(object, row["kind"]),  # type: ignore[arg-type]
                payload=load_json_object(payload),
                turn_id=row["turn_id"],
            ),
            guard=RunWriteGuard(row["aggregate_version"]),
            mode="start",
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
    if command.kind == "resolve_side_effect":
        raise CommandStateError(
            "side effect resolution resume is not assembled for SQLite",
        )
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


async def _allocate_turn_id(
    connection: aiosqlite.Connection,
    session_id: str,
) -> str:
    async with connection.execute(
        "SELECT next_turn_number FROM durable_sessions WHERE session_id = ?",
        (session_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise CheckpointCorruptedError("durable session record is missing")
    next_turn_number = row["next_turn_number"]
    if type(next_turn_number) is not int or next_turn_number < 1:
        raise CheckpointCorruptedError("durable session record is corrupted")
    await connection.execute(
        "UPDATE durable_sessions SET next_turn_number = ? WHERE session_id = ?",
        (next_turn_number + 1, session_id),
    )
    return f"turn_{next_turn_number}"


def _duplicate_receipt(
    row: aiosqlite.Row,
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
        stored_turn_id = row["turn_id"]
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
        if receipt.kind == "cancel":
            if stored_turn_id is not None:
                raise ValueError("turn_id")
        elif type(stored_turn_id) is not str or not stored_turn_id.strip():
            raise TypeError("turn_id")
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
