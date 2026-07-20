from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace

import aiosqlite

from agentos._waiting import WaitReason
from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.tools import SideEffectPolicy
from agentos.durable.sqlite_records import require_run
from agentos.durable.sqlite_side_effect_records import (
    insert_side_effect,
    load_side_effect,
    update_side_effect,
)
from agentos.durable.sqlite_side_effect_transitions import (
    invocation_attempt_id,
    reservation_matches,
    resolved_record,
    retry_resolution_records,
    validate_supersede,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_integrity import result_ref_digest, wait_reason_digest
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectAttemptId,
    SideEffectCompletion,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectRecordConflictError,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectResultIntegrityError,
    SideEffectStatus,
    SideEffectTransitionError,
    WaitingToolCompletion,
)
from agentos.runtime.tool_identity import compensation_operation_id


ConnectionFactory = Callable[
    [],
    AbstractAsyncContextManager[aiosqlite.Connection],
]


class SQLiteSideEffectLedger:
    """与 Durable Store 共享 connection 的 SQLite Side Effect Adapter。"""

    def __init__(
        self,
        transaction: ConnectionFactory,
        read_connection: ConnectionFactory,
    ) -> None:
        self._transaction = transaction
        self._read_connection = read_connection

    async def reserve(
        self,
        *,
        invocation: ToolInvocation,
        policy: SideEffectPolicy,
        invocation_digest: str,
        invocation_ref: ProtectedPayloadRef | None,
        guard: RunWriteGuard,
        supersedes: SideEffectAttemptId | None = None,
    ) -> SideEffectRecord:
        if invocation.context.tenant_id is not None or invocation_ref is None:
            raise SideEffectTransitionError
        attempt_id = invocation_attempt_id(invocation)
        async with self._transaction() as connection:
            await _require_run_guard(
                connection,
                session_id=attempt_id.session_id,
                run_id=invocation.context.run_id,
                guard=guard,
            )
            current = await load_side_effect(
                connection,
                session_id=attempt_id.session_id,
                operation_id=attempt_id.operation_id,
                attempt=None,
            )
            existing = await load_side_effect(
                connection,
                session_id=attempt_id.session_id,
                operation_id=attempt_id.operation_id,
                attempt=attempt_id.attempt,
            )
            if existing is not None:
                if existing != current:
                    raise SideEffectTransitionError
                if reservation_matches(
                    existing,
                    invocation,
                    policy,
                    invocation_digest,
                    invocation_ref,
                ):
                    return existing
                raise SideEffectRecordConflictError
            if supersedes is None:
                if current is not None or attempt_id.attempt != 1:
                    raise SideEffectTransitionError
            else:
                validate_supersede(
                    current,
                    supersedes,
                    invocation,
                    policy,
                    invocation_digest,
                    invocation_ref,
                )
                assert current is not None
                await update_side_effect(
                    connection,
                    replace(
                        current,
                        status=SideEffectStatus.RESOLVED,
                        resolution=SideEffectResolutionOutcome.SUPERSEDED,
                    ),
                )
            record = SideEffectRecord(
                attempt_id=attempt_id,
                run_id=invocation.context.run_id,
                turn_id=invocation.context.turn_id,
                invocation_id=invocation.context.invocation_id,
                tool_name=invocation.tool_name,
                policy=policy,
                status=SideEffectStatus.RESERVED,
                invocation_digest=invocation_digest,
                invocation_ref=invocation_ref,
            )
            await insert_side_effect(connection, record)
            return record

    async def get(
        self,
        *,
        tenant_id: str | None,
        session_id: str,
        operation_id: str,
        attempt: int | None,
        guard: RunWriteGuard,
    ) -> SideEffectRecord | None:
        _require_local_guard(guard)
        if tenant_id is not None:
            return None
        async with self._read_connection() as connection:
            record = await load_side_effect(
                connection,
                session_id=session_id,
                operation_id=operation_id,
                attempt=attempt,
            )
            if record is not None:
                await _require_run_guard(
                    connection,
                    session_id=session_id,
                    run_id=record.run_id,
                    guard=guard,
                    require_running=False,
                )
            return record

    async def mark_started(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await self._transition(
            attempt_id,
            guard,
            expected=SideEffectStatus.RESERVED,
            target=SideEffectStatus.STARTED,
        )

    async def complete(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        completion: SideEffectCompletion,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        if type(completion) is not SideEffectCompletion:
            raise TypeError("completion must be SideEffectCompletion")
        if completion.outcome_kind is SideEffectOutcomeKind.WAIT_CONTROL:
            raise SideEffectTransitionError
        async with self._transaction() as connection:
            current = await self._require_current(connection, attempt_id, guard)
            if current.status is not SideEffectStatus.STARTED:
                raise SideEffectTransitionError
            if (
                completion.outcome_kind is SideEffectOutcomeKind.HANDLER_ERROR
                and current.policy
                not in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
            ):
                raise SideEffectTransitionError
            if completion.result_ref is not None and (
                result_ref_digest(completion.result_ref) != completion.result_digest
            ):
                raise SideEffectResultIntegrityError
            updated = replace(
                current,
                status=SideEffectStatus.COMPLETED,
                result_ref=completion.result_ref,
                result_digest=completion.result_digest,
                outcome_kind=completion.outcome_kind,
                failure_code=completion.failure_code,
            )
            await update_side_effect(connection, updated)
            return updated

    async def mark_ambiguous(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        async with self._transaction() as connection:
            current = await self._require_current(connection, attempt_id, guard)
            if current.status is SideEffectStatus.AMBIGUOUS:
                return current
            if (
                current.status is not SideEffectStatus.STARTED
                or current.policy in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
            ):
                raise SideEffectTransitionError
            updated = replace(current, status=SideEffectStatus.AMBIGUOUS)
            await update_side_effect(connection, updated)
            return updated

    async def begin_compensation(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        async with self._transaction() as connection:
            current = await self._require_current(connection, attempt_id, guard)
            if current.status is not SideEffectStatus.COMPENSATING:
                raise SideEffectTransitionError
            updated = replace(
                current,
                compensation_attempt=(current.compensation_attempt or 0) + 1,
            )
            await update_side_effect(connection, updated)
            return updated

    async def complete_compensation(
        self,
        *,
        attempt_id: CompensationAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        if type(attempt_id) is not CompensationAttemptId:
            raise TypeError("attempt_id must be CompensationAttemptId")
        async with self._transaction() as connection:
            current = await self._require_current(
                connection,
                attempt_id.side_effect_attempt,
                guard,
            )
            if (
                current.status is not SideEffectStatus.COMPENSATING
                or current.compensation_operation_id
                != attempt_id.compensation_operation_id
                or current.compensation_attempt != attempt_id.compensation_attempt
            ):
                raise SideEffectTransitionError
            updated = replace(current, status=SideEffectStatus.COMPENSATED)
            await update_side_effect(connection, updated)
            return updated

    async def resolve(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        resolution: SideEffectResolution,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        if type(resolution) is not SideEffectResolution:
            raise TypeError("resolution must be SideEffectResolution")
        async with self._transaction() as connection:
            current = await self._require_current(connection, attempt_id, guard)
            if (
                current.status is not SideEffectStatus.AMBIGUOUS
                or current.attempt_id.operation_id != resolution.operation_id
            ):
                raise SideEffectTransitionError
            if resolution.kind is SideEffectResolutionKind.ACCEPT_RESULT:
                updated = resolved_record(
                    current,
                    SideEffectResolutionOutcome.ACCEPTED,
                    result_ref=resolution.result_ref,
                    result_digest=resolution.result_digest,
                )
                await update_side_effect(connection, updated)
                return updated
            if resolution.kind is SideEffectResolutionKind.FAIL:
                updated = resolved_record(
                    current,
                    SideEffectResolutionOutcome.FAILED,
                )
                await update_side_effect(connection, updated)
                return updated
            if resolution.kind is SideEffectResolutionKind.COMPENSATE:
                if current.policy is not SideEffectPolicy.COMPENSATABLE:
                    raise SideEffectTransitionError
                updated = replace(
                    current,
                    status=SideEffectStatus.COMPENSATING,
                    compensation_operation_id=compensation_operation_id(
                        current.attempt_id.operation_id,
                    ),
                    compensation_attempt=1,
                )
                await update_side_effect(connection, updated)
                return updated
            return await _resolve_retry(connection, current, resolution)

    async def _transition(
        self,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
        *,
        expected: SideEffectStatus,
        target: SideEffectStatus,
    ) -> SideEffectRecord:
        async with self._transaction() as connection:
            current = await self._require_current(connection, attempt_id, guard)
            if current.status is target:
                return current
            if current.status is not expected:
                raise SideEffectTransitionError
            updated = replace(current, status=target)
            await update_side_effect(connection, updated)
            return updated

    async def _require_current(
        self,
        connection: aiosqlite.Connection,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await _load_current(connection, attempt_id, guard)


async def _load_current(
    connection: aiosqlite.Connection,
    attempt_id: SideEffectAttemptId,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    current = await load_side_effect(
        connection,
        session_id=attempt_id.session_id,
        operation_id=attempt_id.operation_id,
        attempt=None,
    )
    if current is None or current.attempt_id != attempt_id:
        raise SideEffectTransitionError
    await _require_run_guard(
        connection,
        session_id=attempt_id.session_id,
        run_id=current.run_id,
        guard=guard,
    )
    return current


async def _require_run_guard(
    connection: aiosqlite.Connection,
    *,
    session_id: str,
    run_id: str,
    guard: RunWriteGuard,
    require_running: bool = True,
) -> None:
    _require_local_guard(guard)
    run = await require_run(connection, session_id, run_id)
    if (
        (require_running and run.status.value != "running")
        or run.aggregate_version != guard.expected_version
    ):
        raise SideEffectTransitionError


def _require_local_guard(guard: RunWriteGuard) -> None:
    if type(guard) is not RunWriteGuard:
        raise TypeError("guard must be RunWriteGuard")
    if guard.claim_id is not None:
        raise SideEffectTransitionError


async def _resolve_retry(
    connection: aiosqlite.Connection,
    current: SideEffectRecord,
    resolution: SideEffectResolution,
) -> SideEffectRecord:
    resolved, reserved = retry_resolution_records(current, resolution)
    await update_side_effect(connection, resolved)
    await insert_side_effect(connection, reserved)
    return reserved


async def _complete_wait_control_in_transaction(
    connection: aiosqlite.Connection,
    *,
    completion: WaitingToolCompletion,
    session_id: str,
    run_id: str,
    turn_id: str,
    reason: WaitReason,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    """在 SQLiteDurableStore 已拥有的事务中完成 wait-control。"""

    attempt_id = SideEffectAttemptId(
        None,
        session_id,
        completion.operation_id,
        completion.attempt,
    )
    current = await _load_current(connection, attempt_id, guard)
    if (
        current.status is not SideEffectStatus.STARTED
        or current.policy is not SideEffectPolicy.PURE
        or current.run_id != run_id
        or current.turn_id != turn_id
        or current.invocation_id != completion.invocation_id
        or completion.wait_reason_digest != wait_reason_digest(reason)
    ):
        raise SideEffectTransitionError
    updated = replace(
        current,
        status=SideEffectStatus.COMPLETED,
        outcome_kind=SideEffectOutcomeKind.WAIT_CONTROL,
        wait_reason_digest=completion.wait_reason_digest,
    )
    await update_side_effect(connection, updated)
    return updated


__all__ = ["SQLiteSideEffectLedger"]
