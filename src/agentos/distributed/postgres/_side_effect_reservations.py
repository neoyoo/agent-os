from __future__ import annotations

from dataclasses import replace

from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.tools import SideEffectPolicy
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres._side_effect_records import (
    insert_record,
    load_attempt,
    load_current,
    require_guard,
    update_record,
    with_fence,
)
from agentos.durable.sqlite_side_effect_transitions import (
    invocation_attempt_id,
    reservation_matches,
    validate_supersede,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectRecordConflictError,
    SideEffectResolutionOutcome,
    SideEffectStatus,
    SideEffectTransitionError,
)


async def reserve(
    database: PostgresPool,
    *,
    invocation: ToolInvocation,
    policy: SideEffectPolicy,
    invocation_digest: str,
    invocation_ref: ProtectedPayloadRef | None,
    guard: RunWriteGuard,
    supersedes: SideEffectAttemptId | None,
) -> SideEffectRecord:
    if invocation.context.tenant_id is None or invocation_ref is None:
        raise SideEffectTransitionError()
    attempt_id = invocation_attempt_id(invocation)
    async with database.transaction() as connection:
        await require_guard(connection, invocation.context.run_id, attempt_id, guard)
        current = await load_current(connection, attempt_id, lock=True)
        existing = await load_attempt(connection, attempt_id, lock=True)
        if existing is not None:
            if existing != current:
                raise SideEffectTransitionError()
            if reservation_matches(
                existing,
                invocation,
                policy,
                invocation_digest,
                invocation_ref,
            ):
                return existing
            raise SideEffectRecordConflictError()
        if supersedes is None:
            if current is not None or attempt_id.attempt != 1:
                raise SideEffectTransitionError()
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
            await update_record(
                connection,
                with_fence(
                    replace(
                        current,
                        status=SideEffectStatus.RESOLVED,
                        resolution=SideEffectResolutionOutcome.SUPERSEDED,
                    ),
                    guard,
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
            claim_id=guard.claim_id,
            fencing_token=guard.fencing_token,
        )
        await insert_record(connection, record)
        return record


async def get_record(
    database: PostgresPool,
    *,
    tenant_id: str | None,
    session_id: str,
    operation_id: str,
    attempt: int | None,
    guard: RunWriteGuard,
) -> SideEffectRecord | None:
    if tenant_id is None:
        return None
    attempt_id = SideEffectAttemptId(
        tenant_id,
        session_id,
        operation_id,
        1 if attempt is None else attempt,
    )
    async with database.transaction() as connection:
        record = (
            await load_current(connection, attempt_id, lock=True)
            if attempt is None
            else await load_attempt(connection, attempt_id, lock=True)
        )
        if record is not None:
            await require_guard(connection, record.run_id, attempt_id, guard)
        return record


__all__ = ["get_record", "reserve"]
