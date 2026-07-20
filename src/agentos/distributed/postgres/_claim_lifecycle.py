from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from agentos.distributed.errors import (
    ClaimConflictError,
    ClaimExpiredError,
    StaleFenceError,
)
from agentos.distributed.models import ExecutionClaim, RequestScope
from agentos.distributed.postgres._database import PostgresPool, Row, fetchone


async def heartbeat(
    database: PostgresPool,
    *,
    scope: RequestScope,
    claim: ExecutionClaim,
    ttl: timedelta,
) -> ExecutionClaim:
    require_claim_scope(scope, claim)
    seconds = ttl_seconds(ttl)
    async with database.transaction() as connection:
        row = await fetchone(
            connection,
            """
            UPDATE agentos_distributed_sessions
            SET active_claim_expires_at =
                clock_timestamp() + (%s * interval '1 second')
            WHERE tenant_id = %s AND session_id = %s
              AND active_claim_run_id = %s
              AND active_claim_id = %s AND active_claim_owner_id = %s
              AND fencing_token = %s
              AND active_claim_expires_at > clock_timestamp()
            RETURNING active_claim_expires_at
            """,
            (
                seconds,
                scope.tenant_id,
                claim.session_id,
                claim.run_id,
                claim.claim_id,
                claim.owner_id,
                claim.fencing_token,
            ),
        )
        if row is None:
            await _raise_claim_failure(connection, scope, claim)
    return ExecutionClaim(
        claim.tenant_id,
        claim.session_id,
        claim.run_id,
        claim.owner_id,
        claim.claim_id,
        claim.fencing_token,
        cast(datetime, row["active_claim_expires_at"]),
    )


async def release(
    database: PostgresPool,
    *,
    scope: RequestScope,
    claim: ExecutionClaim,
) -> None:
    require_claim_scope(scope, claim)
    async with database.transaction() as connection:
        row = await fetchone(
            connection,
            """
            SELECT fencing_token, active_claim_id, active_claim_owner_id,
                   active_claim_run_id
            FROM agentos_distributed_sessions
            WHERE tenant_id = %s AND session_id = %s
            FOR UPDATE
            """,
            (scope.tenant_id, claim.session_id),
        )
        if not _claim_matches(row, claim):
            raise StaleFenceError()
        await connection.execute(
            """
            UPDATE agentos_distributed_accepted_inputs
            SET status = 'accepted', claim_id = NULL, fencing_token = NULL
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
              AND status = 'claimed' AND claim_id = %s AND fencing_token = %s
            """,
            (
                scope.tenant_id,
                claim.session_id,
                claim.run_id,
                claim.claim_id,
                claim.fencing_token,
            ),
        )
        await _clear_session_claim(connection, scope.tenant_id, claim.session_id)


def ttl_seconds(ttl: timedelta) -> float:
    if type(ttl) is not timedelta or ttl <= timedelta(0):
        raise ValueError("claim ttl must be positive")
    return ttl.total_seconds()


def require_claim_scope(scope: RequestScope, claim: ExecutionClaim) -> None:
    if type(scope) is not RequestScope or type(claim) is not ExecutionClaim:
        raise TypeError("scope and claim must use canonical types")
    if scope.tenant_id != claim.tenant_id:
        raise StaleFenceError()


def _claim_matches(row: Row | None, claim: ExecutionClaim) -> bool:
    return row is not None and (
        row["fencing_token"] == claim.fencing_token
        and row["active_claim_id"] == claim.claim_id
        and row["active_claim_owner_id"] == claim.owner_id
        and row["active_claim_run_id"] == claim.run_id
    )


async def _raise_claim_failure(connection, scope, claim) -> None:  # type: ignore[no-untyped-def]
    row = await fetchone(
        connection,
        """
        SELECT fencing_token, active_claim_id, active_claim_owner_id,
               active_claim_run_id, active_claim_expires_at,
               clock_timestamp() AS database_now
        FROM agentos_distributed_sessions
        WHERE tenant_id = %s AND session_id = %s FOR UPDATE
        """,
        (scope.tenant_id, claim.session_id),
    )
    if not _claim_matches(row, claim):
        raise StaleFenceError()
    assert row is not None
    if row["active_claim_expires_at"] <= row["database_now"]:  # type: ignore[operator]
        raise ClaimExpiredError()
    raise ClaimConflictError()


async def _clear_session_claim(connection, tenant_id: str, session_id: str) -> None:
    await connection.execute(
        """
        UPDATE agentos_distributed_sessions
        SET active_claim_id = NULL, active_claim_owner_id = NULL,
            active_claim_run_id = NULL, active_claim_expires_at = NULL
        WHERE tenant_id = %s AND session_id = %s
        """,
        (tenant_id, session_id),
    )


__all__ = ["heartbeat", "release", "require_claim_scope", "ttl_seconds"]
