from __future__ import annotations

from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import AsyncConnection, fetchone
from agentos.distributed.postgres._reconciliation import (
    hydrate_reconciliation_preparation,
)
from agentos.distributed.postgres._reconciliation_records import cursor_from_row
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.execution import (
    AcceptedTurnInput,
    AcceptedTurnPreparation,
    ApplyAcceptedInput,
    RestoreAcceptedTurn,
    RunExecutionCursor,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunState, RunStatus
from agentos.runtime.side_effect_resolution import side_effect_resolution_from_payload
from agentos.runtime.side_effect_resume import SideEffectResume


async def classify_accepted_turn_preparation(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    run: RunState,
    accepted: AcceptedTurnInput,
    guard: RunWriteGuard,
) -> AcceptedTurnPreparation:
    """按当前 accepted turn 的权威 cursor 分类唯一执行准备。"""

    current_cursor = await _load_current_cursor(
        connection,
        scope=scope,
        run=run,
    )
    if (
        type(accepted) is AcceptedContinuationInput
        and accepted.kind == "resolve_side_effect"
    ):
        try:
            resolution = side_effect_resolution_from_payload(accepted.payload)
        except (TypeError, ValueError):
            raise ClaimConflictError() from None
        preparation = await hydrate_reconciliation_preparation(
            connection,
            scope=scope,
            run=run,
            accepted=accepted,
            resolution=resolution,
            guard=guard,
        )
        if type(preparation) is SideEffectResume:
            if current_cursor is not None:
                raise ClaimConflictError()
            return preparation
        if current_cursor != preparation.cursor:
            raise ClaimConflictError()
        return preparation
    if current_cursor is None:
        return ApplyAcceptedInput()
    if current_cursor.turn_id != accepted.turn_id:
        raise ClaimConflictError()
    return RestoreAcceptedTurn(current_cursor)


async def _load_current_cursor(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    run: RunState,
) -> RunExecutionCursor | None:
    row = await fetchone(
        connection,
        """
        SELECT cursor.payload_json, checkpoint.turn_id,
               checkpoint.aggregate_version
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
        return None
    cursor = cursor_from_row(
        row,
        ClaimConflictError,
        require_pending=False,
    )
    if (
        run.status is not RunStatus.RUNNING
        or row["turn_id"] != cursor.turn_id
        or row["aggregate_version"] != run.aggregate_version
    ):
        raise ClaimConflictError()
    return cursor


__all__ = ["classify_accepted_turn_preparation"]
