from __future__ import annotations

from agentos._json_values import thaw_json_value
from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import AsyncConnection, Row, fetchone
from agentos.distributed.postgres._reconciliation_records import (
    cursor_contains_record,
    cursor_from_row,
)
from agentos.distributed.postgres._side_effect_records import (
    load_current,
    load_attempt,
    require_current,
    update_record,
    with_fence,
)
from agentos.distributed.postgres._side_effect_transitions import (
    begin_compensation_current,
    resolve_current,
)
from agentos.durable.serialization import dump_json
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.execution import RestoreAcceptedTurn, RunExecutionCursor
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunState, RunStatus
from agentos.runtime.side_effect_resume import SideEffectResume
from agentos.runtime.side_effect_resolution import side_effect_resolution_to_payload
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectStatus,
)


async def hydrate_reconciliation_preparation(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    run: RunState,
    accepted: AcceptedContinuationInput,
    resolution: SideEffectResolution,
    guard: RunWriteGuard,
) -> SideEffectResume | RestoreAcceptedTurn:
    row = await _lock_source_for_command(
        connection,
        scope=scope,
        run=run,
        accepted=accepted,
        resolution=resolution,
    )
    cursor = cursor_from_row(row, ClaimConflictError)
    current = await load_current(
        connection,
        SideEffectAttemptId(
            scope.tenant_id,
            run.session_id,
            resolution.operation_id,
            1,
        ),
        lock=True,
    )
    if current is None:
        raise ClaimConflictError()
    if row["consumed_checkpoint_id"] is not None:
        if run.status is not RunStatus.RUNNING:
            raise ClaimConflictError()
        continuation_cursor = await _validate_consumed_recovery(
            connection,
            scope=scope,
            run=run,
            accepted=accepted,
            row=row,
        )
        await _validate_resolution_lineage(
            connection,
            current=current,
            resolution=resolution,
        )
        current = with_fence(current, guard)
        await update_record(connection, current)
        return RestoreAcceptedTurn(continuation_cursor)
    applied_command_id = row["resolution_command_id"]
    if applied_command_id is None:
        if (
            run.status is not RunStatus.QUEUED
            or current.status is not SideEffectStatus.AMBIGUOUS
        ):
            raise ClaimConflictError()
        try:
            current = await resolve_current(
                connection,
                current.attempt_id,
                resolution,
                guard,
            )
        except Exception as error:
            raise ClaimConflictError() from error
        updated = await fetchone(
            connection,
            """
            UPDATE agentos_distributed_reconciliation_sources
            SET resolution_command_id = %s
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
              AND waiting_aggregate_version = %s
              AND resolution_command_id IS NULL
            RETURNING resolution_command_id
            """,
            (
                accepted.command_id,
                scope.tenant_id,
                run.session_id,
                run.run_id,
                row["waiting_aggregate_version"],
            ),
        )
        if updated is None:
            raise ClaimConflictError()
    elif applied_command_id == accepted.command_id:
        current = await _recover_applied_record(
            connection,
            current=current,
            guard=guard,
        )
    else:
        raise ClaimConflictError()
    await _validate_resolution_lineage(
        connection,
        current=current,
        resolution=resolution,
    )
    try:
        return SideEffectResume(
            tenant_id=scope.tenant_id,
            session_id=run.session_id,
            run_id=run.run_id,
            continuation_turn_id=accepted.turn_id,
            source_cursor=cursor,
            record=current,
            resolution=resolution,
        )
    except (TypeError, ValueError):
        raise ClaimConflictError() from None


async def validate_reconciliation_resume(
    connection: AsyncConnection,
    *,
    resume: SideEffectResume,
    guard: RunWriteGuard,
) -> None:
    if resume.tenant_id is None:
        raise ClaimConflictError()
    current = await require_current(connection, resume.record.attempt_id, guard)
    await _validate_resolution_lineage(
        connection,
        current=current,
        resolution=resume.resolution,
    )
    row = await fetchone(
        connection,
        """
        SELECT source.*, command.session_id AS command_session_id,
               command.run_id AS command_run_id, command.kind AS command_kind,
               command.payload_json AS command_payload_json,
               command.turn_id AS command_turn_id,
               command.aggregate_version AS command_aggregate_version,
               source_checkpoint.turn_id AS source_turn_id,
               source_checkpoint.aggregate_version AS source_aggregate_version,
               waiting_checkpoint.turn_id AS waiting_turn_id,
               waiting_checkpoint.aggregate_version AS waiting_checkpoint_version
        FROM agentos_distributed_reconciliation_sources AS source
        JOIN agentos_distributed_commands AS command
          ON command.tenant_id = source.tenant_id
         AND command.command_id = source.resolution_command_id
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
          AND command.turn_id = %s
          AND source.consumed_checkpoint_id IS NULL
        FOR UPDATE OF source, command, source_checkpoint, waiting_checkpoint
        """,
        (
            resume.tenant_id,
            resume.session_id,
            resume.run_id,
            resume.resolution.operation_id,
            resume.continuation_turn_id,
        ),
    )
    if row is None:
        raise ClaimConflictError()
    cursor = cursor_from_row(row, ClaimConflictError)
    if (
        current != resume.record
        or current.claim_id != guard.claim_id
        or current.fencing_token != guard.fencing_token
        or cursor != resume.source_cursor
        or not _source_row_is_consistent(row, resume, cursor)
    ):
        raise ClaimConflictError()


async def _lock_source_for_command(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    run: RunState,
    accepted: AcceptedContinuationInput,
    resolution: SideEffectResolution,
) -> Row:
    row = await fetchone(
        connection,
        """
        SELECT source.*, command.session_id AS command_session_id,
               command.run_id AS command_run_id, command.kind AS command_kind,
               command.payload_json AS command_payload_json,
               command.turn_id AS command_turn_id,
               command.aggregate_version AS command_aggregate_version,
               source_checkpoint.turn_id AS source_turn_id,
               source_checkpoint.aggregate_version AS source_aggregate_version,
               waiting_checkpoint.turn_id AS waiting_turn_id,
               waiting_checkpoint.aggregate_version AS waiting_checkpoint_version
        FROM agentos_distributed_reconciliation_sources AS source
        JOIN agentos_distributed_commands AS command
          ON command.tenant_id = source.tenant_id
         AND command.command_id = %s
         AND command.session_id = source.session_id
         AND command.run_id = source.run_id
         AND command.aggregate_version = source.waiting_aggregate_version + 1
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
        LEFT JOIN agentos_distributed_checkpoints AS consumed_checkpoint
          ON consumed_checkpoint.tenant_id = source.tenant_id
         AND consumed_checkpoint.session_id = source.session_id
         AND consumed_checkpoint.run_id = source.run_id
         AND consumed_checkpoint.checkpoint_id = source.consumed_checkpoint_id
        WHERE source.tenant_id = %s AND source.session_id = %s
          AND source.run_id = %s AND source.operation_id = %s
        FOR UPDATE OF source, command, source_checkpoint, waiting_checkpoint
        """,
        (
            accepted.command_id,
            scope.tenant_id,
            run.session_id,
            run.run_id,
            resolution.operation_id,
        ),
    )
    expected_payload = dump_json(thaw_json_value(accepted.payload))
    if (
        row is None
        or run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}
        or row["command_session_id"] != run.session_id
        or row["command_run_id"] != run.run_id
        or row["command_kind"] != "resolve_side_effect"
        or row["command_payload_json"] != expected_payload
        or row["command_turn_id"] != accepted.turn_id
        or row["waiting_checkpoint_version"] != row["waiting_aggregate_version"]
        or row["source_aggregate_version"] + 1 != row["waiting_aggregate_version"]
        or (
            run.status is RunStatus.QUEUED
            and run.aggregate_version != row["command_aggregate_version"]
        )
        or (
            run.status is RunStatus.RUNNING
            and run.aggregate_version <= row["command_aggregate_version"]
        )
    ):
        raise ClaimConflictError()
    cursor = cursor_from_row(row, ClaimConflictError)
    if (
        row["source_turn_id"] != cursor.turn_id
        or row["waiting_turn_id"] != cursor.turn_id
    ):
        raise ClaimConflictError()
    return row


async def _validate_consumed_recovery(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    run: RunState,
    accepted: AcceptedContinuationInput,
    row: Row,
) -> RunExecutionCursor:
    cursor_row = await fetchone(
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
    if cursor_row is None:
        raise ClaimConflictError()
    cursor = cursor_from_row(
        cursor_row,
        ClaimConflictError,
        require_pending=False,
    )
    if (
        cursor.turn_id != accepted.turn_id
        or cursor_row["turn_id"] != accepted.turn_id
        or cursor_row["aggregate_version"] != run.aggregate_version
        or row["consumed_at"] is None
    ):
        raise ClaimConflictError()
    return cursor


async def _recover_applied_record(
    connection: AsyncConnection,
    *,
    current: object,
    guard: RunWriteGuard,
):  # type: ignore[no-untyped-def]
    from agentos.runtime.side_effect_types import SideEffectRecord

    if type(current) is not SideEffectRecord:
        raise ClaimConflictError()
    if current.status is SideEffectStatus.COMPENSATING:
        try:
            return await begin_compensation_current(
                connection,
                current.attempt_id,
                guard,
            )
        except Exception:
            raise ClaimConflictError() from None
    current = with_fence(current, guard)
    await update_record(connection, current)
    return current


async def _validate_resolution_lineage(
    connection: AsyncConnection,
    *,
    current: SideEffectRecord,
    resolution: SideEffectResolution,
) -> None:
    if resolution.kind is not SideEffectResolutionKind.RETRY_PROVEN_SAFE:
        return
    attempt = current.attempt_id.attempt
    if attempt <= 1:
        raise ClaimConflictError()
    previous = await load_attempt(
        connection,
        SideEffectAttemptId(
            current.attempt_id.tenant_id,
            current.attempt_id.session_id,
            current.attempt_id.operation_id,
            attempt - 1,
        ),
        lock=True,
    )
    if (
        previous is None
        or previous.status is not SideEffectStatus.RESOLVED
        or previous.resolution is not SideEffectResolutionOutcome.RETRY_SAFE
        or previous.attestation_ref != resolution.attestation_ref
        or previous.attestation_digest != resolution.attestation_digest
    ):
        raise ClaimConflictError()


def _source_row_is_consistent(row: Row, resume: SideEffectResume, cursor) -> bool:  # type: ignore[no-untyped-def]
    return (
        row["command_session_id"] == resume.session_id
        and row["command_run_id"] == resume.run_id
        and row["command_kind"] == "resolve_side_effect"
        and row["command_turn_id"] == resume.continuation_turn_id
        and row["operation_id"] == resume.resolution.operation_id
        and row["source_turn_id"] == cursor.turn_id
        and row["waiting_turn_id"] == cursor.turn_id
        and row["waiting_checkpoint_version"] == row["waiting_aggregate_version"]
        and row["source_aggregate_version"] + 1 == row["waiting_aggregate_version"]
        and row["command_aggregate_version"] == row["waiting_aggregate_version"] + 1
        and row["command_payload_json"]
        == dump_json(thaw_json_value(side_effect_resolution_to_payload(
            resume.resolution,
        )))
        and cursor_contains_record(cursor, resume.record)
    )


__all__ = [
    "hydrate_reconciliation_preparation",
    "validate_reconciliation_resume",
]
