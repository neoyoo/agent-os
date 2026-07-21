from __future__ import annotations

from datetime import datetime
from typing import cast

from agentos.distributed._execution_outcomes import CommittedExecutionOutcome
from agentos.distributed.errors import CheckpointConflictError, ClaimConflictError
from agentos.distributed.postgres._claim_records import target_from_row
from agentos.distributed.postgres._database import PostgresPool, Row, fetchall
from agentos.distributed.postgres._identities import outbox_id as expected_outbox_id


async def resolve_committed_outcome(
    database: PostgresPool,
    *,
    outbox_id: str,
) -> CommittedExecutionOutcome | None:
    async with database.connection() as connection:
        rows = await fetchall(
            connection,
            """
            SELECT o.outbox_id, o.tenant_id, o.principal_id, o.session_id,
                   o.payload ->> 'kind' AS outbox_kind,
                   o.payload ->> 'turn_id' AS outbox_turn_id,
                   o.payload ->> 'fencing_token' AS outbox_fencing_token,
                   o.payload ->> 'recovery_id' AS outbox_recovery_id,
                   r.run_id, r.status, r.wait_kind, r.wait_handle,
                   r.wait_detail, r.wait_not_before, r.aggregate_version,
                   input.source_kind AS input_source_kind,
                   input.source_id AS input_source_id,
                   input.status AS input_status,
                   input.turn_id AS input_turn_id,
                   checkpoint.turn_id AS checkpoint_turn_id,
                   checkpoint.fencing_token AS checkpoint_fencing_token,
                   checkpoint.aggregate_version AS checkpoint_aggregate_version,
                   checkpoint.created_at AS checkpoint_created_at
            FROM agentos_distributed_outbox AS o
            JOIN agentos_distributed_runs AS r
              ON r.tenant_id = o.tenant_id
             AND r.session_id = o.session_id
             AND r.run_id = o.run_id
            JOIN agentos_distributed_accepted_inputs AS input
              ON input.tenant_id = o.tenant_id
             AND input.session_id = o.session_id
             AND input.run_id = o.run_id
             AND input.status = 'committed'
            JOIN agentos_distributed_checkpoints AS checkpoint
              ON checkpoint.tenant_id = input.tenant_id
             AND checkpoint.session_id = input.session_id
             AND checkpoint.run_id = input.run_id
             AND checkpoint.checkpoint_id = input.checkpoint_id
            WHERE o.outbox_id = %s
              AND o.topic = 'agentos.run.execution'
            ORDER BY input.accepted_at, input.turn_id
            """,
            (outbox_id,),
        )
    matches = [row for row in rows if _matches_outbox(row, outbox_id)]
    if not matches:
        return None
    if len(matches) != 1:
        raise ClaimConflictError()
    return _outcome_from_row(matches[0])


def input_matches_outbox(
    row: Row,
    delivery: Row,
    *,
    tenant_id: str,
    outbox_id: str,
    current_fencing_token: int,
) -> bool:
    """验证 accepted input 是否由指定 execution outbox 唯一投递。"""

    try:
        source_kind = row["source_kind"]
        source_id = row["source_id"]
        if type(source_kind) is not str or type(source_id) is not str:
            raise TypeError
        if delivery["outbox_kind"] == "recover":
            return _matches_recover_outbox(
                delivery,
                input_turn_id=row["turn_id"],
                tenant_id=tenant_id,
                outbox_id=outbox_id,
                current_fencing_token=current_fencing_token,
            )
        return expected_outbox_id(tenant_id, source_kind, source_id) == outbox_id
    except (KeyError, TypeError, ValueError):
        raise ClaimConflictError() from None


def _matches_outbox(row: Row, outbox_id: str) -> bool:
    try:
        tenant_id = row["tenant_id"]
        source_kind = row["input_source_kind"]
        source_id = row["input_source_id"]
        if any(type(value) is not str for value in (tenant_id, source_kind, source_id)):
            raise TypeError
        if row["outbox_kind"] == "recover":
            return _matches_recover_outbox(
                row,
                input_turn_id=row["input_turn_id"],
                tenant_id=tenant_id,
                outbox_id=outbox_id,
                current_fencing_token=None,
            )
        return expected_outbox_id(tenant_id, source_kind, source_id) == outbox_id
    except (KeyError, TypeError, ValueError):
        raise ClaimConflictError() from None


def _matches_recover_outbox(
    row: Row,
    *,
    input_turn_id: object,
    tenant_id: str,
    outbox_id: str,
    current_fencing_token: int | None,
) -> bool:
    turn_id = row["outbox_turn_id"]
    recovery_id = row["outbox_recovery_id"]
    raw_fence = row["outbox_fencing_token"]
    if (
        type(input_turn_id) is not str
        or type(turn_id) is not str
        or type(recovery_id) is not str
        or type(raw_fence) is not str
    ):
        raise ClaimConflictError()
    try:
        recovery_fence = int(raw_fence)
    except ValueError:
        raise ClaimConflictError() from None
    if recovery_fence < 1:
        raise ClaimConflictError()
    if current_fencing_token is not None and recovery_fence != current_fencing_token:
        return False
    return (
        turn_id == input_turn_id
        and expected_outbox_id(tenant_id, "recover", recovery_id) == outbox_id
    )


def _outcome_from_row(row: Row) -> CommittedExecutionOutcome:
    try:
        input_turn_id = row["input_turn_id"]
        checkpoint_turn_id = row["checkpoint_turn_id"]
        if (
            row["input_status"] != "committed"
            or type(input_turn_id) is not str
            or checkpoint_turn_id != input_turn_id
        ):
            raise ValueError
        attempt = row["checkpoint_fencing_token"]
        version = row["checkpoint_aggregate_version"]
        committed_at = row["checkpoint_created_at"]
        if type(attempt) is not int or type(version) is not int:
            raise TypeError
        if type(committed_at) is not datetime:
            raise TypeError
        return CommittedExecutionOutcome(
            target=target_from_row(row),
            turn_id=input_turn_id,
            execution_attempt=attempt,
            committed_version=version,
            committed_at=cast(datetime, committed_at),
        )
    except (KeyError, TypeError, ValueError, CheckpointConflictError):
        raise ClaimConflictError() from None


__all__ = [
    "input_matches_outbox",
    "resolve_committed_outcome",
]
