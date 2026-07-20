from __future__ import annotations

from agentos._waiting import WaitReason
from agentos.distributed.errors import CheckpointConflictError, ClaimConflictError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import AsyncConnection, fetchone
from agentos.distributed.postgres._reconciliation_records import (
    cursor_contains_record,
    cursor_from_row,
)
from agentos.distributed.postgres._side_effect_records import load_current
from agentos.durable.serialization import execution_cursor_to_json
from agentos.runtime.checkpoint import RunCheckpoint
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunState
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectResolution,
    SideEffectStatus,
)


async def validate_reconciliation_command_source(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    run: RunState,
    resolution: SideEffectResolution,
    record: SideEffectRecord,
) -> None:
    row = await fetchone(
        connection,
        """
        SELECT source.*,
               source_checkpoint.turn_id AS source_turn_id,
               source_checkpoint.aggregate_version AS source_aggregate_version,
               waiting_checkpoint.turn_id AS waiting_turn_id,
               waiting_checkpoint.aggregate_version AS waiting_checkpoint_version
        FROM agentos_distributed_reconciliation_sources AS source
        JOIN agentos_distributed_checkpoints AS source_checkpoint
          ON source_checkpoint.tenant_id = source.tenant_id
         AND source_checkpoint.session_id = source.session_id
         AND source_checkpoint.run_id = source.run_id
         AND source_checkpoint.checkpoint_id = source.source_checkpoint_id
        JOIN agentos_distributed_checkpoints AS waiting_checkpoint
          ON waiting_checkpoint.tenant_id = source.tenant_id
         AND waiting_checkpoint.session_id = source.session_id
         AND waiting_checkpoint.run_id = source.run_id
         AND waiting_checkpoint.checkpoint_id = source.waiting_checkpoint_id
        WHERE source.tenant_id = %s AND source.session_id = %s
          AND source.run_id = %s AND source.operation_id = %s
          AND source.waiting_aggregate_version = %s
          AND source.resolution_command_id IS NULL
          AND source.consumed_checkpoint_id IS NULL
        FOR UPDATE OF source, source_checkpoint, waiting_checkpoint
        """,
        (
            scope.tenant_id,
            run.session_id,
            run.run_id,
            resolution.operation_id,
            run.aggregate_version,
        ),
    )
    if row is None:
        raise ClaimConflictError()
    cursor = cursor_from_row(row, ClaimConflictError)
    if (
        row["waiting_checkpoint_version"] != run.aggregate_version
        or row["source_aggregate_version"] + 1 != run.aggregate_version
        or row["source_turn_id"] != cursor.turn_id
        or row["waiting_turn_id"] != cursor.turn_id
        or not cursor_contains_record(cursor, record)
    ):
        raise ClaimConflictError()


async def capture_reconciliation_source(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    run: RunState,
    reason: WaitReason,
    waiting_checkpoint: RunCheckpoint,
    guard: RunWriteGuard,
) -> None:
    if reason.kind != "side_effect_reconciliation":
        return
    row = await fetchone(
        connection,
        """
        SELECT cursor.payload_json, cursor.checkpoint_id,
               checkpoint.turn_id, checkpoint.aggregate_version
        FROM agentos_distributed_execution_cursors AS cursor
        JOIN agentos_distributed_checkpoints AS checkpoint
          ON checkpoint.tenant_id = cursor.tenant_id
         AND checkpoint.session_id = cursor.session_id
         AND checkpoint.run_id = cursor.run_id
         AND checkpoint.checkpoint_id = cursor.checkpoint_id
        WHERE cursor.tenant_id = %s AND cursor.session_id = %s
          AND cursor.run_id = %s
        FOR UPDATE OF cursor, checkpoint
        """,
        (scope.tenant_id, run.session_id, run.run_id),
    )
    if row is None:
        raise CheckpointConflictError()
    cursor = cursor_from_row(row, CheckpointConflictError)
    current = await load_current(
        connection,
        SideEffectAttemptId(
            scope.tenant_id,
            run.session_id,
            reason.handle,
            1,
        ),
        lock=True,
    )
    if (
        current is None
        or current.status is not SideEffectStatus.AMBIGUOUS
        or current.run_id != run.run_id
        or current.turn_id != cursor.turn_id
        or current.claim_id != guard.claim_id
        or current.fencing_token != guard.fencing_token
        or row["turn_id"] != cursor.turn_id
        or row["aggregate_version"] != run.aggregate_version
        or waiting_checkpoint.aggregate_version != run.aggregate_version + 1
        or waiting_checkpoint.turn_id != cursor.turn_id
        or not cursor_contains_record(cursor, current)
    ):
        raise CheckpointConflictError()
    await connection.execute(
        """
        INSERT INTO agentos_distributed_reconciliation_sources
            (tenant_id, session_id, run_id, operation_id,
             waiting_aggregate_version, source_checkpoint_id,
             waiting_checkpoint_id, cursor_payload_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            scope.tenant_id,
            run.session_id,
            run.run_id,
            reason.handle,
            waiting_checkpoint.aggregate_version,
            row["checkpoint_id"],
            waiting_checkpoint.checkpoint_id,
            execution_cursor_to_json(cursor),
        ),
    )


async def consume_reconciliation_source(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    run_id: str,
    turn_id: str,
    checkpoint: RunCheckpoint,
    guard: RunWriteGuard,
) -> None:
    accepted = await fetchone(
        connection,
        """
        SELECT source_id
        FROM agentos_distributed_accepted_inputs
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
          AND turn_id = %s AND source_kind = 'command'
          AND continuation_kind = 'resolve_side_effect'
          AND status = 'claimed' AND claim_id = %s AND fencing_token = %s
        FOR UPDATE
        """,
        (
            scope.tenant_id,
            checkpoint.session_id,
            run_id,
            turn_id,
            guard.claim_id,
            guard.fencing_token,
        ),
    )
    if accepted is None:
        return
    consumed = await fetchone(
        connection,
        """
        UPDATE agentos_distributed_reconciliation_sources AS source
        SET consumed_checkpoint_id = %s, consumed_at = clock_timestamp()
        FROM agentos_distributed_commands AS command
        WHERE source.tenant_id = %s AND source.session_id = %s
          AND source.run_id = %s AND source.resolution_command_id = %s
          AND source.consumed_checkpoint_id IS NULL
          AND command.tenant_id = source.tenant_id
          AND command.command_id = source.resolution_command_id
          AND command.turn_id = %s
          AND command.aggregate_version = source.waiting_aggregate_version + 1
        RETURNING source.operation_id
        """,
        (
            checkpoint.checkpoint_id,
            scope.tenant_id,
            checkpoint.session_id,
            run_id,
            accepted["source_id"],
            turn_id,
        ),
    )
    if consumed is None:
        raise CheckpointConflictError()


__all__ = [
    "capture_reconciliation_source",
    "consume_reconciliation_source",
    "validate_reconciliation_command_source",
]
