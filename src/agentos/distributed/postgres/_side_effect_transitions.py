from __future__ import annotations

from dataclasses import replace

from agentos.capabilities.tools import SideEffectPolicy
from agentos.distributed.postgres._database import AsyncConnection, PostgresPool
from agentos.distributed.postgres._side_effect_records import (
    insert_record,
    require_current,
    update_record,
    with_fence,
)
from agentos.durable.sqlite_side_effect_transitions import (
    resolved_record,
    retry_resolution_records,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectAttemptId,
    SideEffectCompletion,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectResultIntegrityError,
    SideEffectStatus,
    SideEffectTransitionError,
)
from agentos.runtime.tool_identity import compensation_operation_id


async def mark_started(
    database: PostgresPool,
    *,
    attempt_id: SideEffectAttemptId,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    return await _transition(
        database,
        attempt_id,
        guard,
        expected=SideEffectStatus.RESERVED,
        target=SideEffectStatus.STARTED,
    )


async def complete(
    database: PostgresPool,
    *,
    attempt_id: SideEffectAttemptId,
    completion: SideEffectCompletion,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    if completion.outcome_kind is SideEffectOutcomeKind.WAIT_CONTROL:
        raise SideEffectTransitionError()
    async with database.transaction() as connection:
        current = await require_current(connection, attempt_id, guard)
        if current.status is not SideEffectStatus.STARTED:
            raise SideEffectTransitionError()
        if (
            completion.outcome_kind is SideEffectOutcomeKind.HANDLER_ERROR
            and current.policy not in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
        ):
            raise SideEffectTransitionError()
        if completion.result_ref is not None and (
            result_ref_digest(completion.result_ref) != completion.result_digest
        ):
            raise SideEffectResultIntegrityError()
        updated = with_fence(
            replace(
                current,
                status=SideEffectStatus.COMPLETED,
                result_ref=completion.result_ref,
                result_digest=completion.result_digest,
                outcome_kind=completion.outcome_kind,
                failure_code=completion.failure_code,
            ),
            guard,
        )
        await update_record(connection, updated)
        return updated


async def mark_ambiguous(
    database: PostgresPool,
    *,
    attempt_id: SideEffectAttemptId,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    async with database.transaction() as connection:
        current = await require_current(connection, attempt_id, guard)
        if current.status is SideEffectStatus.AMBIGUOUS:
            return current
        if (
            current.status is not SideEffectStatus.STARTED
            or current.policy in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
        ):
            raise SideEffectTransitionError()
        updated = with_fence(
            replace(current, status=SideEffectStatus.AMBIGUOUS),
            guard,
        )
        await update_record(connection, updated)
        return updated


async def begin_compensation(
    database: PostgresPool,
    *,
    attempt_id: SideEffectAttemptId,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    async with database.transaction() as connection:
        return await begin_compensation_current(
            connection,
            attempt_id,
            guard,
        )


async def begin_compensation_current(
    connection: AsyncConnection,
    attempt_id: SideEffectAttemptId,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    current = await require_current(connection, attempt_id, guard)
    if current.status is not SideEffectStatus.COMPENSATING:
        raise SideEffectTransitionError()
    updated = with_fence(
        replace(
            current,
            compensation_attempt=(current.compensation_attempt or 0) + 1,
        ),
        guard,
    )
    await update_record(connection, updated)
    return updated


async def complete_compensation(
    database: PostgresPool,
    *,
    attempt_id: CompensationAttemptId,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    async with database.transaction() as connection:
        current = await require_current(
            connection,
            attempt_id.side_effect_attempt,
            guard,
        )
        if (
            current.status is not SideEffectStatus.COMPENSATING
            or current.compensation_operation_id != attempt_id.compensation_operation_id
            or current.compensation_attempt != attempt_id.compensation_attempt
        ):
            raise SideEffectTransitionError()
        updated = with_fence(
            replace(current, status=SideEffectStatus.COMPENSATED),
            guard,
        )
        await update_record(connection, updated)
        return updated


async def resolve(
    database: PostgresPool,
    *,
    attempt_id: SideEffectAttemptId,
    resolution: SideEffectResolution,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    async with database.transaction() as connection:
        return await resolve_current(connection, attempt_id, resolution, guard)


async def resolve_current(
    connection: AsyncConnection,
    attempt_id: SideEffectAttemptId,
    resolution: SideEffectResolution,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    current = await require_current(connection, attempt_id, guard)
    if (
        current.status is not SideEffectStatus.AMBIGUOUS
        or current.attempt_id.operation_id != resolution.operation_id
    ):
        raise SideEffectTransitionError()
    if resolution.kind is SideEffectResolutionKind.ACCEPT_RESULT:
        updated = resolved_record(
            current,
            SideEffectResolutionOutcome.ACCEPTED,
            result_ref=resolution.result_ref,
            result_digest=resolution.result_digest,
        )
    elif resolution.kind is SideEffectResolutionKind.FAIL:
        updated = resolved_record(current, SideEffectResolutionOutcome.FAILED)
    elif resolution.kind is SideEffectResolutionKind.COMPENSATE:
        if current.policy is not SideEffectPolicy.COMPENSATABLE:
            raise SideEffectTransitionError()
        updated = replace(
            current,
            status=SideEffectStatus.COMPENSATING,
            compensation_operation_id=compensation_operation_id(
                current.attempt_id.operation_id,
            ),
            compensation_attempt=1,
        )
    else:
        resolved, reserved = retry_resolution_records(current, resolution)
        await update_record(connection, with_fence(resolved, guard))
        reserved = with_fence(reserved, guard)
        await insert_record(connection, reserved)
        return reserved
    updated = with_fence(updated, guard)
    await update_record(connection, updated)
    return updated


async def _transition(
    database: PostgresPool,
    attempt_id: SideEffectAttemptId,
    guard: RunWriteGuard,
    *,
    expected: SideEffectStatus,
    target: SideEffectStatus,
) -> SideEffectRecord:
    async with database.transaction() as connection:
        current = await require_current(connection, attempt_id, guard)
        if current.status is target:
            return current
        if current.status is not expected:
            raise SideEffectTransitionError()
        updated = with_fence(replace(current, status=target), guard)
        await update_record(connection, updated)
        return updated


__all__ = [
    "begin_compensation",
    "begin_compensation_current",
    "complete",
    "complete_compensation",
    "mark_ambiguous",
    "mark_started",
    "resolve",
    "resolve_current",
]
