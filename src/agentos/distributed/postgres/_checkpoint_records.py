from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import uuid4

from agentos.distributed.errors import CheckpointConflictError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import AsyncConnection, Row, fetchone
from agentos.distributed.postgres._records import (
    session_checkpoint_from_json,
    session_checkpoint_to_json,
)
from agentos.durable.serialization import execution_cursor_to_json
from agentos.runtime.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    RunCheckpoint,
    SessionCheckpoint,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunState


async def write_checkpoint(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    checkpoint: SessionCheckpoint,
    run_id: str,
    turn_id: str,
    aggregate_version: int,
    fencing_token: int,
) -> RunCheckpoint:
    if not turn_id:
        raise CheckpointConflictError()
    payload = session_checkpoint_to_json(checkpoint)
    await _validate_message_history(
        connection,
        scope.tenant_id,
        checkpoint.session_id,
        checkpoint,
    )
    checkpoint_id = f"checkpoint_{uuid4().hex}"
    row = await fetchone(
        connection,
        """
        INSERT INTO agentos_distributed_checkpoints
            (tenant_id, checkpoint_id, session_id, run_id, turn_id,
             aggregate_version, snapshot_json, schema_version, fencing_token)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING created_at
        """,
        (
            scope.tenant_id,
            checkpoint_id,
            checkpoint.session_id,
            run_id,
            turn_id,
            aggregate_version,
            payload,
            CHECKPOINT_SCHEMA_VERSION,
            fencing_token,
        ),
    )
    if row is None:
        raise CheckpointConflictError()
    await connection.execute(
        """
        UPDATE agentos_distributed_sessions
        SET status = %s, next_turn_number = %s
        WHERE tenant_id = %s AND session_id = %s
        """,
        (
            checkpoint.session_status,
            checkpoint.next_turn_number,
            scope.tenant_id,
            checkpoint.session_id,
        ),
    )
    cursor = checkpoint.execution_cursor
    if cursor is not None:
        await connection.execute(
            """
            INSERT INTO agentos_distributed_execution_cursors
                (tenant_id, session_id, run_id, payload_json, checkpoint_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, session_id, run_id) DO UPDATE
            SET payload_json = excluded.payload_json,
                checkpoint_id = excluded.checkpoint_id
            """,
            (
                scope.tenant_id,
                checkpoint.session_id,
                run_id,
                execution_cursor_to_json(cursor),
                checkpoint_id,
            ),
        )
    return RunCheckpoint(
        checkpoint_id,
        checkpoint.session_id,
        run_id,
        turn_id,
        aggregate_version,
        cast(datetime, row["created_at"]),
    )


async def commit_input(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    checkpoint_id: str,
    guard: RunWriteGuard,
) -> None:
    row = await fetchone(
        connection,
        """
        UPDATE agentos_distributed_accepted_inputs
        SET status = 'committed', claim_id = NULL, fencing_token = NULL,
            checkpoint_id = %s
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
          AND status = 'claimed' AND claim_id = %s AND fencing_token = %s
        RETURNING turn_id
        """,
        (
            checkpoint_id,
            scope.tenant_id,
            session_id,
            run_id,
            guard.claim_id,
            guard.fencing_token,
        ),
    )
    if row is None:
        raise CheckpointConflictError()


async def clear_cursor(
    connection: AsyncConnection,
    tenant_id: str,
    session_id: str,
    run_id: str,
) -> None:
    await connection.execute(
        """
        DELETE FROM agentos_distributed_execution_cursors
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
        """,
        (tenant_id, session_id, run_id),
    )


async def write_run(
    connection: AsyncConnection,
    tenant_id: str,
    state: RunState,
    *,
    result_content: str | None,
) -> None:
    reason = state.wait_reason
    await connection.execute(
        """
        UPDATE agentos_distributed_runs
        SET status = %s, wait_kind = %s, wait_handle = %s, wait_detail = %s,
            wait_not_before = %s, aggregate_version = %s,
            result_content = %s, updated_at = clock_timestamp()
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
        """,
        (
            state.status.value,
            None if reason is None else reason.kind,
            None if reason is None else reason.handle,
            None if reason is None else reason.detail,
            None if reason is None else reason.not_before,
            state.aggregate_version,
            result_content,
            tenant_id,
            state.session_id,
            state.run_id,
        ),
    )


def run_checkpoint_from_row(row: Row) -> RunCheckpoint:
    try:
        return RunCheckpoint(
            checkpoint_id=row["checkpoint_id"],  # type: ignore[arg-type]
            session_id=row["session_id"],  # type: ignore[arg-type]
            run_id=row["run_id"],  # type: ignore[arg-type]
            turn_id=row["turn_id"],  # type: ignore[arg-type]
            aggregate_version=row["aggregate_version"],  # type: ignore[arg-type]
            created_at=row["created_at"],  # type: ignore[arg-type]
            schema_version=row["schema_version"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointConflictError() from None


async def _validate_message_history(
    connection: AsyncConnection,
    tenant_id: str,
    session_id: str,
    checkpoint: SessionCheckpoint,
) -> None:
    row = await fetchone(
        connection,
        """
        SELECT snapshot_json FROM agentos_distributed_checkpoints
        WHERE tenant_id = %s AND session_id = %s
          AND snapshot_json IS NOT NULL
        ORDER BY checkpoint_sequence DESC LIMIT 1
        """,
        (tenant_id, session_id),
    )
    if row is None:
        return
    payload = row["snapshot_json"]
    if type(payload) is not str:
        raise CheckpointConflictError()
    previous = session_checkpoint_from_json(payload)
    if (
        len(checkpoint.messages) < len(previous.messages)
        or checkpoint.messages[:len(previous.messages)] != previous.messages
        or checkpoint.next_turn_number < previous.next_turn_number
    ):
        raise CheckpointConflictError()


__all__ = [
    "clear_cursor",
    "commit_input",
    "run_checkpoint_from_row",
    "write_checkpoint",
    "write_run",
]
