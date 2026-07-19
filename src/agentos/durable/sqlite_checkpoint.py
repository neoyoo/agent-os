from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from agentos.durable.serialization import (
    context_from_json,
    context_to_json,
    execution_cursor_from_json,
    execution_cursor_to_json,
    message_from_json,
    message_to_json,
)
from agentos.durable.sqlite_records import row_to_checkpoint
from agentos.runtime.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    RunCheckpoint,
    SessionCheckpoint,
)
from agentos.runtime.errors import CheckpointConflictError, CheckpointCorruptedError


ContextWriter = Callable[[sqlite3.Connection, SessionCheckpoint], None]


def write_checkpoint_state(
    connection: sqlite3.Connection,
    snapshot: SessionCheckpoint,
    *,
    run_id: str,
    turn_id: str,
    aggregate_version: int,
    created_at: datetime,
    write_context: ContextWriter,
) -> RunCheckpoint:
    connection.execute(
        "UPDATE durable_sessions SET status = ?, next_turn_number = ? "
        "WHERE session_id = ?",
        (snapshot.session_status, snapshot.next_turn_number, snapshot.session_id),
    )
    _write_messages(connection, snapshot)
    _write_active_refs(connection, snapshot)
    write_context(connection, snapshot)
    _write_execution_cursor(connection, snapshot, run_id=run_id)
    checkpoint = RunCheckpoint(
        checkpoint_id=f"checkpoint_{uuid4().hex}",
        session_id=snapshot.session_id,
        run_id=run_id,
        turn_id=turn_id,
        aggregate_version=aggregate_version,
        created_at=created_at,
    )
    connection.execute(
        "INSERT INTO durable_checkpoints "
        "(checkpoint_id, session_id, run_id, turn_id, "
        "aggregate_version, created_at, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            checkpoint.checkpoint_id,
            checkpoint.session_id,
            checkpoint.run_id,
            checkpoint.turn_id,
            checkpoint.aggregate_version,
            checkpoint.created_at.isoformat(),
            CHECKPOINT_SCHEMA_VERSION,
        ),
    )
    return checkpoint


def write_context_state(
    connection: sqlite3.Connection,
    snapshot: SessionCheckpoint,
) -> None:
    connection.execute(
        "INSERT INTO durable_context_states (session_id, payload_json) "
        "VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET "
        "payload_json = excluded.payload_json",
        (snapshot.session_id, context_to_json(snapshot.context)),
    )


def load_checkpoint(
    connection: sqlite3.Connection,
    session_id: str,
) -> SessionCheckpoint | None:
    latest = connection.execute(
        "SELECT * FROM durable_checkpoints WHERE session_id = ? "
        "ORDER BY rowid DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if latest is None:
        return None
    row_to_checkpoint(latest)
    session = connection.execute(
        "SELECT * FROM durable_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    context = connection.execute(
        "SELECT payload_json FROM durable_context_states WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    cursor = connection.execute(
        "SELECT payload_json FROM durable_execution_cursors "
        "WHERE session_id = ? AND run_id = ?",
        (session_id, latest["run_id"]),
    ).fetchone()
    if session is None or context is None:
        raise CheckpointCorruptedError("checkpoint state is incomplete")
    try:
        messages = tuple(
            message_from_json(row["payload_json"])
            for row in connection.execute(
                "SELECT payload_json FROM durable_messages "
                "WHERE session_id = ? ORDER BY position",
                (session_id,),
            )
        )
        active_refs = tuple(
            row["message_id"]
            for row in connection.execute(
                "SELECT message_id FROM durable_active_refs "
                "WHERE session_id = ? ORDER BY position",
                (session_id,),
            )
        )
        payload = context["payload_json"]
        if not isinstance(payload, str):
            raise TypeError("context payload")
        return SessionCheckpoint(
            session_id=session_id,
            session_status=session["status"],
            next_turn_number=session["next_turn_number"],
            messages=messages,
            active_refs=active_refs,
            context=context_from_json(payload),
            execution_cursor=(
                None
                if cursor is None
                else execution_cursor_from_json(cursor["payload_json"])
            ),
        )
    except CheckpointCorruptedError:
        raise
    except (KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError("checkpoint state is corrupted") from None


def latest_checkpoint(
    connection: sqlite3.Connection,
    session_id: str,
    run_id: str,
) -> RunCheckpoint | None:
    row = connection.execute(
        "SELECT * FROM durable_checkpoints WHERE session_id = ? "
        "AND run_id = ? ORDER BY rowid DESC LIMIT 1",
        (session_id, run_id),
    ).fetchone()
    return None if row is None else row_to_checkpoint(row)


def _write_messages(
    connection: sqlite3.Connection,
    snapshot: SessionCheckpoint,
) -> None:
    for position, message in enumerate(snapshot.messages):
        payload = message_to_json(message)
        existing = connection.execute(
            "SELECT payload_json, position FROM durable_messages "
            "WHERE session_id = ? AND message_id = ?",
            (snapshot.session_id, message.id),
        ).fetchone()
        if existing is not None and (
            existing["payload_json"] != payload or existing["position"] != position
        ):
            raise CheckpointConflictError("stored message checkpoint conflict")
        connection.execute(
            "INSERT OR IGNORE INTO durable_messages "
            "(session_id, message_id, position, payload_json) VALUES (?, ?, ?, ?)",
            (snapshot.session_id, message.id, position, payload),
        )


def _write_active_refs(
    connection: sqlite3.Connection,
    snapshot: SessionCheckpoint,
) -> None:
    connection.execute(
        "DELETE FROM durable_active_refs WHERE session_id = ?",
        (snapshot.session_id,),
    )
    for position, message_id in enumerate(snapshot.active_refs):
        connection.execute(
            "INSERT INTO durable_active_refs "
            "(session_id, position, message_id) VALUES (?, ?, ?)",
            (snapshot.session_id, position, message_id),
        )


def _write_execution_cursor(
    connection: sqlite3.Connection,
    snapshot: SessionCheckpoint,
    *,
    run_id: str,
) -> None:
    cursor = snapshot.execution_cursor
    if cursor is None:
        connection.execute(
            "DELETE FROM durable_execution_cursors "
            "WHERE session_id = ? AND run_id = ?",
            (snapshot.session_id, run_id),
        )
        return
    connection.execute(
        "INSERT INTO durable_execution_cursors "
        "(session_id, run_id, payload_json) VALUES (?, ?, ?) "
        "ON CONFLICT(session_id, run_id) DO UPDATE SET "
        "payload_json = excluded.payload_json",
        (
            snapshot.session_id,
            run_id,
            execution_cursor_to_json(cursor),
        ),
    )
