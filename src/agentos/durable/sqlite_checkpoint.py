from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from uuid import uuid4

import aiosqlite

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


ContextWriter = Callable[
    [aiosqlite.Connection, SessionCheckpoint],
    Awaitable[None],
]


async def write_checkpoint_state(
    connection: aiosqlite.Connection,
    snapshot: SessionCheckpoint,
    *,
    run_id: str,
    turn_id: str,
    aggregate_version: int,
    created_at: datetime,
    write_context: ContextWriter,
) -> RunCheckpoint:
    await connection.execute(
        "UPDATE durable_sessions SET status = ?, next_turn_number = ? "
        "WHERE session_id = ?",
        (snapshot.session_status, snapshot.next_turn_number, snapshot.session_id),
    )
    await _write_messages(connection, snapshot)
    await _write_active_refs(connection, snapshot)
    await write_context(connection, snapshot)
    await _write_execution_cursor(connection, snapshot, run_id=run_id)
    checkpoint = RunCheckpoint(
        checkpoint_id=f"checkpoint_{uuid4().hex}",
        session_id=snapshot.session_id,
        run_id=run_id,
        turn_id=turn_id,
        aggregate_version=aggregate_version,
        created_at=created_at,
    )
    await connection.execute(
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


async def write_context_state(
    connection: aiosqlite.Connection,
    snapshot: SessionCheckpoint,
) -> None:
    await connection.execute(
        "INSERT INTO durable_context_states (session_id, payload_json) "
        "VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET "
        "payload_json = excluded.payload_json",
        (snapshot.session_id, context_to_json(snapshot.context)),
    )


async def load_checkpoint(
    connection: aiosqlite.Connection,
    session_id: str,
) -> SessionCheckpoint | None:
    latest = await _fetchone(
        connection,
        "SELECT * FROM durable_checkpoints WHERE session_id = ? "
        "ORDER BY rowid DESC LIMIT 1",
        (session_id,),
    )
    if latest is None:
        return None
    row_to_checkpoint(latest)
    session = await _fetchone(
        connection,
        "SELECT * FROM durable_sessions WHERE session_id = ?",
        (session_id,),
    )
    context = await _fetchone(
        connection,
        "SELECT payload_json FROM durable_context_states WHERE session_id = ?",
        (session_id,),
    )
    cursor = await _fetchone(
        connection,
        "SELECT payload_json FROM durable_execution_cursors "
        "WHERE session_id = ? AND run_id = ?",
        (session_id, latest["run_id"]),
    )
    if session is None or context is None:
        raise CheckpointCorruptedError("checkpoint state is incomplete")
    try:
        message_rows = await _fetchall(
            connection,
            "SELECT payload_json FROM durable_messages "
            "WHERE session_id = ? ORDER BY position",
            (session_id,),
        )
        messages = tuple(
            message_from_json(row["payload_json"]) for row in message_rows
        )
        active_rows = await _fetchall(
            connection,
            "SELECT message_id FROM durable_active_refs "
            "WHERE session_id = ? ORDER BY position",
            (session_id,),
        )
        active_refs = tuple(
            row["message_id"] for row in active_rows
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


async def latest_checkpoint(
    connection: aiosqlite.Connection,
    session_id: str,
    run_id: str,
) -> RunCheckpoint | None:
    row = await _fetchone(
        connection,
        "SELECT * FROM durable_checkpoints WHERE session_id = ? "
        "AND run_id = ? ORDER BY rowid DESC LIMIT 1",
        (session_id, run_id),
    )
    return None if row is None else row_to_checkpoint(row)


async def _write_messages(
    connection: aiosqlite.Connection,
    snapshot: SessionCheckpoint,
) -> None:
    for position, message in enumerate(snapshot.messages):
        payload = message_to_json(message)
        existing = await _fetchone(
            connection,
            "SELECT payload_json, position FROM durable_messages "
            "WHERE session_id = ? AND message_id = ?",
            (snapshot.session_id, message.id),
        )
        if existing is not None and (
            existing["payload_json"] != payload or existing["position"] != position
        ):
            raise CheckpointConflictError("stored message checkpoint conflict")
        await connection.execute(
            "INSERT OR IGNORE INTO durable_messages "
            "(session_id, message_id, position, payload_json) VALUES (?, ?, ?, ?)",
            (snapshot.session_id, message.id, position, payload),
        )


async def _write_active_refs(
    connection: aiosqlite.Connection,
    snapshot: SessionCheckpoint,
) -> None:
    await connection.execute(
        "DELETE FROM durable_active_refs WHERE session_id = ?",
        (snapshot.session_id,),
    )
    for position, message_id in enumerate(snapshot.active_refs):
        await connection.execute(
            "INSERT INTO durable_active_refs "
            "(session_id, position, message_id) VALUES (?, ?, ?)",
            (snapshot.session_id, position, message_id),
        )


async def _write_execution_cursor(
    connection: aiosqlite.Connection,
    snapshot: SessionCheckpoint,
    *,
    run_id: str,
) -> None:
    cursor = snapshot.execution_cursor
    if cursor is None:
        await connection.execute(
            "DELETE FROM durable_execution_cursors "
            "WHERE session_id = ? AND run_id = ?",
            (snapshot.session_id, run_id),
        )
        return
    await connection.execute(
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


async def _fetchone(
    connection: aiosqlite.Connection,
    sql: str,
    parameters: Sequence[object],
) -> aiosqlite.Row | None:
    async with connection.execute(sql, parameters) as cursor:
        return await cursor.fetchone()


async def _fetchall(
    connection: aiosqlite.Connection,
    sql: str,
    parameters: Sequence[object],
) -> list[aiosqlite.Row]:
    async with connection.execute(sql, parameters) as cursor:
        return await cursor.fetchall()
