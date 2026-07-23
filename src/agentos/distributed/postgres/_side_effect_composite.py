from __future__ import annotations

from dataclasses import replace

from agentos._waiting import WaitReason
from agentos.capabilities.tools import SideEffectPolicy
from agentos.distributed.errors import CheckpointConflictError, SideEffectInFlightError
from agentos.distributed.postgres._database import AsyncConnection, fetchall
from agentos.distributed.postgres._side_effect_records import (
    record_from_row,
    require_current,
    update_record,
    with_fence,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_cancel import plan_side_effect_cancel
from agentos.runtime.side_effect_integrity import wait_reason_digest
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectInFlightError as RuntimeSideEffectInFlightError,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectResolutionOutcome,
    SideEffectStatus,
    SideEffectTransitionError,
    WaitingToolCompletion,
)


async def complete_wait_control(
    connection: AsyncConnection,
    *,
    completion: WaitingToolCompletion,
    tenant_id: str,
    session_id: str,
    run_id: str,
    turn_id: str,
    reason: WaitReason,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    attempt_id = SideEffectAttemptId(
        tenant_id,
        session_id,
        completion.operation_id,
        completion.attempt,
    )
    current = await require_current(connection, attempt_id, guard)
    if (
        current.status is not SideEffectStatus.STARTED
        or current.policy is not SideEffectPolicy.PURE
        or current.run_id != run_id
        or current.turn_id != turn_id
        or current.invocation_id != completion.invocation_id
        or completion.wait_reason_digest != wait_reason_digest(reason)
    ):
        raise SideEffectTransitionError()
    updated = with_fence(
        replace(
            current,
            status=SideEffectStatus.COMPLETED,
            outcome_kind=SideEffectOutcomeKind.WAIT_CONTROL,
            wait_reason_digest=completion.wait_reason_digest,
        ),
        guard,
    )
    await update_record(connection, updated)
    return updated


async def apply_cancel_safe_stop(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
) -> None:
    records = await _lock_current_records(
        connection,
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
    )
    try:
        plan = plan_side_effect_cancel(records)
    except RuntimeSideEffectInFlightError:
        raise SideEffectInFlightError() from None
    by_id = {record.attempt_id: record for record in records}
    for attempt_id in plan.cancel_before_start:
        await update_record(
            connection,
            replace(
                by_id[attempt_id],
                status=SideEffectStatus.RESOLVED,
                resolution=SideEffectResolutionOutcome.CANCELLED_BEFORE_START,
            ),
        )


async def ensure_terminal_safe_stop(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
    status: RunStatus,
) -> None:
    records = await _lock_current_records(
        connection,
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
    )
    if any(not _terminal_allowed(record, status) for record in records):
        raise CheckpointConflictError()


async def _lock_current_records(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
) -> tuple[SideEffectRecord, ...]:
    rows = await fetchall(
        connection,
        """
        SELECT effect.*
        FROM agentos_distributed_side_effects AS effect
        JOIN (
            SELECT operation_id, MAX(attempt) AS attempt
            FROM agentos_distributed_side_effects
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
            GROUP BY operation_id
        ) AS current
          ON current.operation_id = effect.operation_id
         AND current.attempt = effect.attempt
        WHERE effect.tenant_id = %s AND effect.session_id = %s
          AND effect.run_id = %s
        FOR UPDATE OF effect
        """,
        (tenant_id, session_id, run_id, tenant_id, session_id, run_id),
    )
    return tuple(record_from_row(row) for row in rows)


def _terminal_allowed(record: SideEffectRecord, status: RunStatus) -> bool:
    if status is RunStatus.COMPLETED:
        return (
            record.status is SideEffectStatus.COMPLETED
            and record.outcome_kind
            in {
                SideEffectOutcomeKind.PROVIDER_RESULT,
                SideEffectOutcomeKind.WAIT_CONTROL,
            }
        ) or (
            record.status is SideEffectStatus.RESOLVED
            and record.resolution is SideEffectResolutionOutcome.ACCEPTED
        )
    if status is not RunStatus.FAILED:
        return False
    if record.status in {
        SideEffectStatus.STARTED,
        SideEffectStatus.AMBIGUOUS,
        SideEffectStatus.COMPENSATING,
    }:
        return False
    if record.status is SideEffectStatus.COMPLETED:
        return True
    if record.status is SideEffectStatus.RESOLVED:
        return record.resolution in {
            SideEffectResolutionOutcome.ACCEPTED,
            SideEffectResolutionOutcome.FAILED,
        }
    return record.status in {
        SideEffectStatus.RESERVED,
        SideEffectStatus.COMPENSATED,
    }


__all__ = [
    "apply_cancel_safe_stop",
    "complete_wait_control",
    "ensure_terminal_safe_stop",
]
