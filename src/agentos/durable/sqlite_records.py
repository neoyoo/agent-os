from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from agentos._waiting import WaitReason
from agentos.runtime.checkpoint import RunCheckpoint
from agentos.runtime.errors import CheckpointCorruptedError
from agentos.runtime.run_state import RunNotFoundError, RunState, RunStatus


async def select_run(
    connection: aiosqlite.Connection,
    session_id: str,
    run_id: str,
) -> aiosqlite.Row | None:
    async with connection.execute(
        "SELECT * FROM durable_runs WHERE session_id = ? AND run_id = ?",
        (session_id, run_id),
    ) as cursor:
        return await cursor.fetchone()


async def require_run(
    connection: aiosqlite.Connection,
    session_id: str,
    run_id: str,
) -> RunState:
    row = await select_run(connection, session_id, run_id)
    if row is None:
        raise RunNotFoundError(f"run not found: {run_id}")
    return row_to_state(row)


async def insert_run(connection: aiosqlite.Connection, state: RunState) -> None:
    await connection.execute(
        "INSERT INTO durable_runs "
        "(session_id, run_id, status, aggregate_version) VALUES (?, ?, ?, ?)",
        (state.session_id, state.run_id, state.status.value, state.aggregate_version),
    )


async def update_run(
    connection: aiosqlite.Connection,
    state: RunState,
    *,
    recovery_error: str | None = None,
) -> None:
    reason = state.wait_reason
    await connection.execute(
        "UPDATE durable_runs SET status = ?, wait_kind = ?, wait_handle = ?, "
        "wait_detail = ?, wait_not_before = ?, aggregate_version = ?, "
        "recovery_error = ? WHERE session_id = ? AND run_id = ?",
        (
            state.status.value,
            None if reason is None else reason.kind,
            None if reason is None else reason.handle,
            None if reason is None else reason.detail,
            (
                None
                if reason is None or reason.not_before is None
                else reason.not_before.isoformat()
            ),
            state.aggregate_version,
            recovery_error,
            state.session_id,
            state.run_id,
        ),
    )


def row_to_state(row: aiosqlite.Row) -> RunState:
    try:
        reason = None
        if row["wait_kind"] is not None:
            due = row["wait_not_before"]
            if due is not None and not isinstance(due, str):
                raise TypeError("wait_not_before")
            reason = WaitReason(
                kind=row["wait_kind"],
                handle=row["wait_handle"],
                detail=row["wait_detail"],
                not_before=None if due is None else datetime.fromisoformat(due),
            )
        return RunState(
            run_id=row["run_id"],
            session_id=row["session_id"],
            status=RunStatus(row["status"]),
            wait_reason=reason,
            aggregate_version=row["aggregate_version"],
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError("durable run record is corrupted") from None


def row_to_checkpoint(row: aiosqlite.Row) -> RunCheckpoint:
    try:
        return RunCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            session_id=row["session_id"],
            run_id=row["run_id"],
            turn_id=row["turn_id"],
            aggregate_version=row["aggregate_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            schema_version=row["schema_version"],
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError(
            "durable checkpoint record is corrupted",
        ) from None


def normalize_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("durable clock must return timezone-aware datetime")
    return value.astimezone(UTC)


async def recover_abandoned_running(
    connection: aiosqlite.Connection,
    session_id: str,
) -> tuple[RunState, ...]:
    """把遗留 RUNNING Run fail-closed 为 FAILED。"""

    async with connection.execute(
        "SELECT * FROM durable_runs WHERE session_id = ? AND status = ? "
        "ORDER BY run_id",
        (session_id, RunStatus.RUNNING.value),
    ) as cursor:
        rows = await cursor.fetchall()
    recovered = []
    for row in rows:
        updated = row_to_state(row).transition(RunStatus.FAILED)
        await connection.execute(
            "DELETE FROM durable_execution_cursors "
            "WHERE session_id = ? AND run_id = ?",
            (updated.session_id, updated.run_id),
        )
        await update_run(
            connection,
            updated,
            recovery_error="abandoned running run failed closed",
        )
        recovered.append(updated)
    return tuple(recovered)
