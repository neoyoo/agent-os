from __future__ import annotations

from dataclasses import replace

from agentos.distributed.postgres._database import AsyncConnection, Row, fetchone
from agentos.distributed.postgres._guards import lock_fenced_run
from agentos.distributed.postgres._side_effect_codec import (
    side_effect_record_from_json,
    side_effect_record_to_json,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectTransitionError,
)


async def require_current(
    connection: AsyncConnection,
    attempt_id: SideEffectAttemptId,
    guard: RunWriteGuard,
) -> SideEffectRecord:
    candidate = await load_current(connection, attempt_id, lock=False)
    if candidate is None or candidate.attempt_id != attempt_id:
        raise SideEffectTransitionError()
    await require_guard(connection, candidate.run_id, attempt_id, guard)
    current = await load_current(connection, attempt_id, lock=True)
    if (
        current is None
        or current.attempt_id != attempt_id
        or current.run_id != candidate.run_id
    ):
        raise SideEffectTransitionError()
    return current


async def require_guard(
    connection: AsyncConnection,
    run_id: str,
    attempt_id: SideEffectAttemptId,
    guard: RunWriteGuard,
) -> None:
    if attempt_id.tenant_id is None:
        raise SideEffectTransitionError()
    await lock_fenced_run(
        connection,
        tenant_id=attempt_id.tenant_id,
        session_id=attempt_id.session_id,
        run_id=run_id,
        guard=guard,
    )


async def load_current(
    connection: AsyncConnection,
    attempt_id: SideEffectAttemptId,
    *,
    lock: bool,
) -> SideEffectRecord | None:
    suffix = " FOR UPDATE" if lock else ""
    row = await fetchone(
        connection,
        """
        SELECT * FROM agentos_distributed_side_effects
        WHERE tenant_id = %s AND session_id = %s AND operation_id = %s
        ORDER BY attempt DESC LIMIT 1
        """ + suffix,
        (attempt_id.tenant_id, attempt_id.session_id, attempt_id.operation_id),
    )
    return None if row is None else record_from_row(row)


async def load_attempt(
    connection: AsyncConnection,
    attempt_id: SideEffectAttemptId,
    *,
    lock: bool,
) -> SideEffectRecord | None:
    suffix = " FOR UPDATE" if lock else ""
    row = await fetchone(
        connection,
        """
        SELECT * FROM agentos_distributed_side_effects
        WHERE tenant_id = %s AND session_id = %s AND operation_id = %s
          AND attempt = %s
        """ + suffix,
        (
            attempt_id.tenant_id,
            attempt_id.session_id,
            attempt_id.operation_id,
            attempt_id.attempt,
        ),
    )
    return None if row is None else record_from_row(row)


async def insert_record(
    connection: AsyncConnection,
    record: SideEffectRecord,
) -> None:
    await connection.execute(
        """
        INSERT INTO agentos_distributed_side_effects
            (tenant_id, session_id, operation_id, attempt, run_id, status, payload_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            record.attempt_id.tenant_id,
            record.attempt_id.session_id,
            record.attempt_id.operation_id,
            record.attempt_id.attempt,
            record.run_id,
            record.status.value,
            side_effect_record_to_json(record),
        ),
    )


async def update_record(
    connection: AsyncConnection,
    record: SideEffectRecord,
) -> None:
    await connection.execute(
        """
        UPDATE agentos_distributed_side_effects
        SET status = %s, payload_json = %s
        WHERE tenant_id = %s AND session_id = %s AND operation_id = %s
          AND attempt = %s
        """,
        (
            record.status.value,
            side_effect_record_to_json(record),
            record.attempt_id.tenant_id,
            record.attempt_id.session_id,
            record.attempt_id.operation_id,
            record.attempt_id.attempt,
        ),
    )


def record_from_row(row: Row) -> SideEffectRecord:
    payload = row["payload_json"]
    if type(payload) is not str:
        raise SideEffectTransitionError()
    record = side_effect_record_from_json(payload)
    if (
        record.attempt_id.tenant_id != row["tenant_id"]
        or record.attempt_id.session_id != row["session_id"]
        or record.attempt_id.operation_id != row["operation_id"]
        or record.attempt_id.attempt != row["attempt"]
        or record.run_id != row["run_id"]
        or record.status.value != row["status"]
    ):
        raise SideEffectTransitionError()
    return record


def with_fence(record: SideEffectRecord, guard: RunWriteGuard) -> SideEffectRecord:
    return replace(
        record,
        claim_id=guard.claim_id,
        fencing_token=guard.fencing_token,
    )


__all__ = [
    "insert_record",
    "load_attempt",
    "load_current",
    "record_from_row",
    "require_current",
    "require_guard",
    "update_record",
    "with_fence",
]
