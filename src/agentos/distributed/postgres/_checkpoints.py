from __future__ import annotations

from typing import cast

from agentos._waiting import WaitReason
from agentos.distributed.errors import CheckpointConflictError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._checkpoint_records import (
    clear_cursor,
    commit_input,
    run_checkpoint_from_row,
    write_checkpoint,
    write_run,
)
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres._guards import clear_claim, lock_fenced_run
from agentos.distributed.postgres._outbox_records import STATUS_TOPIC, insert_outbox
from agentos.distributed.postgres._records import (
    run_state_from_row,
    session_checkpoint_from_json,
    terminal_result_from_checkpoint,
)
from agentos.distributed.postgres._reconciliation_sources import (
    capture_reconciliation_source,
    consume_reconciliation_source,
)
from agentos.distributed.postgres._side_effect_composite import (
    apply_cancel_safe_stop,
    complete_wait_control,
    ensure_terminal_safe_stop,
)
from agentos.runtime.checkpoint import RunCheckpoint, SessionCheckpoint
from agentos.runtime.run_commit import RunTerminalStatus
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_types import WaitingToolCompletion


async def commit_running(
    database: PostgresPool,
    scope: RequestScope,
    *,
    checkpoint: SessionCheckpoint,
    run_id: str,
    turn_id: str,
    guard: RunWriteGuard,
) -> RunCheckpoint:
    cursor = checkpoint.execution_cursor
    if cursor is None or cursor.turn_id != turn_id:
        raise CheckpointConflictError()
    async with database.transaction() as connection:
        row = await lock_fenced_run(
            connection,
            tenant_id=scope.tenant_id,
            session_id=checkpoint.session_id,
            run_id=run_id,
            guard=guard,
        )
        current = run_state_from_row(row)
        if current.status is not RunStatus.RUNNING:
            raise CheckpointConflictError()
        committed = await write_checkpoint(
            connection,
            scope=scope,
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            aggregate_version=current.aggregate_version + 1,
            fencing_token=cast(int, guard.fencing_token),
        )
        await consume_reconciliation_source(
            connection,
            scope=scope,
            run_id=run_id,
            turn_id=turn_id,
            checkpoint=committed,
            guard=guard,
        )
        await connection.execute(
            """
            UPDATE agentos_distributed_runs
            SET aggregate_version = %s, updated_at = clock_timestamp()
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
            """,
            (
                committed.aggregate_version,
                scope.tenant_id,
                checkpoint.session_id,
                run_id,
            ),
        )
        return committed


async def commit_waiting(
    database: PostgresPool,
    scope: RequestScope,
    *,
    checkpoint: SessionCheckpoint,
    run_id: str,
    turn_id: str,
    reason: WaitReason,
    guard: RunWriteGuard,
    completion: WaitingToolCompletion | None,
) -> RunCheckpoint:
    if checkpoint.execution_cursor is not None:
        raise CheckpointConflictError()
    async with database.transaction() as connection:
        row = await lock_fenced_run(
            connection,
            tenant_id=scope.tenant_id,
            session_id=checkpoint.session_id,
            run_id=run_id,
            guard=guard,
        )
        current = run_state_from_row(row)
        if completion is not None:
            await complete_wait_control(
                connection,
                completion=completion,
                tenant_id=scope.tenant_id,
                session_id=checkpoint.session_id,
                run_id=run_id,
                turn_id=turn_id,
                reason=reason,
                guard=guard,
            )
        updated = current.transition(RunStatus.WAITING, wait_reason=reason)
        committed = await write_checkpoint(
            connection,
            scope=scope,
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            aggregate_version=updated.aggregate_version,
            fencing_token=cast(int, guard.fencing_token),
        )
        await capture_reconciliation_source(
            connection,
            scope=scope,
            run=current,
            reason=reason,
            waiting_checkpoint=committed,
            guard=guard,
        )
        await commit_input(
            connection,
            scope=scope,
            session_id=checkpoint.session_id,
            run_id=run_id,
            checkpoint_id=committed.checkpoint_id,
            guard=guard,
        )
        await clear_cursor(
            connection,
            scope.tenant_id,
            checkpoint.session_id,
            run_id,
        )
        await write_run(
            connection,
            scope.tenant_id,
            updated,
            result_content=None,
        )
        await clear_claim(
            connection,
            tenant_id=scope.tenant_id,
            session_id=checkpoint.session_id,
        )
        await insert_outbox(
            connection,
            scope=scope,
            session_id=checkpoint.session_id,
            run_id=run_id,
            source_kind="waiting",
            source_id=f"{run_id}:{updated.aggregate_version}",
            topic=STATUS_TOPIC,
            payload={"status": "waiting"},
        )
        return committed


async def commit_terminal(
    database: PostgresPool,
    scope: RequestScope,
    *,
    checkpoint: SessionCheckpoint,
    run_id: str,
    turn_id: str,
    status: RunTerminalStatus,
    guard: RunWriteGuard,
) -> RunCheckpoint:
    if checkpoint.execution_cursor is not None:
        raise CheckpointConflictError()
    terminal = RunStatus(status)
    result = terminal_result_from_checkpoint(checkpoint, terminal)
    async with database.transaction() as connection:
        row = await lock_fenced_run(
            connection,
            tenant_id=scope.tenant_id,
            session_id=checkpoint.session_id,
            run_id=run_id,
            guard=guard,
        )
        current = run_state_from_row(row)
        if terminal is RunStatus.CANCELLED:
            await apply_cancel_safe_stop(
                connection,
                tenant_id=scope.tenant_id,
                session_id=checkpoint.session_id,
                run_id=run_id,
            )
        else:
            await ensure_terminal_safe_stop(
                connection,
                tenant_id=scope.tenant_id,
                session_id=checkpoint.session_id,
                run_id=run_id,
                status=terminal,
            )
        updated = current.transition(terminal)
        committed = await write_checkpoint(
            connection,
            scope=scope,
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            aggregate_version=updated.aggregate_version,
            fencing_token=cast(int, guard.fencing_token),
        )
        await commit_input(
            connection,
            scope=scope,
            session_id=checkpoint.session_id,
            run_id=run_id,
            checkpoint_id=committed.checkpoint_id,
            guard=guard,
        )
        await clear_cursor(
            connection,
            scope.tenant_id,
            checkpoint.session_id,
            run_id,
        )
        await write_run(
            connection,
            scope.tenant_id,
            updated,
            result_content=None if result is None else result.content,
        )
        await clear_claim(
            connection,
            tenant_id=scope.tenant_id,
            session_id=checkpoint.session_id,
        )
        await insert_outbox(
            connection,
            scope=scope,
            session_id=checkpoint.session_id,
            run_id=run_id,
            source_kind="terminal",
            source_id=f"{run_id}:{updated.aggregate_version}:{status}",
            topic=STATUS_TOPIC,
            payload={"status": status},
        )
        return committed


async def load_checkpoint(
    database: PostgresPool,
    scope: RequestScope,
    session_id: str,
) -> SessionCheckpoint | None:
    async with database.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT snapshot_json FROM agentos_distributed_checkpoints
            WHERE tenant_id = %s AND session_id = %s AND snapshot_json IS NOT NULL
            ORDER BY checkpoint_sequence DESC LIMIT 1
            """,
            (scope.tenant_id, session_id),
        )
    if row is None:
        return None
    payload = row["snapshot_json"]
    if type(payload) is not str:
        raise CheckpointConflictError()
    checkpoint = session_checkpoint_from_json(payload)
    if checkpoint.session_id != session_id:
        raise CheckpointConflictError()
    return checkpoint


async def latest_checkpoint(
    database: PostgresPool,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> RunCheckpoint | None:
    async with database.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT * FROM agentos_distributed_checkpoints
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
            ORDER BY aggregate_version DESC LIMIT 1
            """,
            (scope.tenant_id, session_id, run_id),
        )
    return None if row is None else run_checkpoint_from_row(row)


__all__ = [
    "commit_running",
    "commit_terminal",
    "commit_waiting",
    "latest_checkpoint",
    "load_checkpoint",
]
