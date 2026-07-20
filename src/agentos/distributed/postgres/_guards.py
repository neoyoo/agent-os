from __future__ import annotations

from datetime import datetime

from agentos.distributed.errors import (
    CheckpointConflictError,
    ClaimExpiredError,
    RunNotFoundError,
    StaleFenceError,
)
from agentos.distributed.postgres._database import AsyncConnection, Row, fetchone
from agentos.runtime.run_runtime import RunWriteGuard


async def lock_fenced_run(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
    guard: RunWriteGuard,
) -> Row:
    if type(guard) is not RunWriteGuard:
        raise TypeError("guard must be RunWriteGuard")
    if guard.claim_id is None or guard.fencing_token is None:
        raise StaleFenceError()
    row = await lock_run_with_session(
        connection,
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
    )
    if (
        row["session_fencing_token"] != guard.fencing_token
        or row["active_claim_id"] != guard.claim_id
        or row["active_claim_run_id"] != run_id
    ):
        raise StaleFenceError()
    expires_at = row["active_claim_expires_at"]
    database_now = row["database_now"]
    if (
        type(expires_at) is not datetime
        or type(database_now) is not datetime
        or expires_at <= database_now
    ):
        raise ClaimExpiredError()
    if row["aggregate_version"] != guard.expected_version:
        raise CheckpointConflictError()
    return row


async def lock_run_with_session(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
) -> Row:
    session = await fetchone(
        connection,
        """
        SELECT fencing_token, fencing_token AS session_fencing_token,
               active_claim_id, active_claim_run_id, active_claim_expires_at
        FROM agentos_distributed_sessions
        WHERE tenant_id = %s AND session_id = %s
        FOR UPDATE
        """,
        (tenant_id, session_id),
    )
    if session is None:
        raise RunNotFoundError()
    run = await fetchone(
        connection,
        """
        SELECT *, clock_timestamp() AS database_now
        FROM agentos_distributed_runs
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
        FOR UPDATE
        """,
        (tenant_id, session_id, run_id),
    )
    if run is None:
        raise RunNotFoundError()
    return {**run, **session}


async def clear_claim(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
) -> None:
    await connection.execute(
        """
        UPDATE agentos_distributed_sessions
        SET active_claim_id = NULL,
            active_claim_owner_id = NULL,
            active_claim_run_id = NULL,
            active_claim_expires_at = NULL
        WHERE tenant_id = %s AND session_id = %s
        """,
        (tenant_id, session_id),
    )


__all__ = ["clear_claim", "lock_fenced_run", "lock_run_with_session"]
