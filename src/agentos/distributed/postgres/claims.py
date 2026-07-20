from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import (
    ClaimedExecution,
    ExecutionClaim,
    RequestScope,
    RunDeliveryTarget,
)
from agentos.distributed.postgres._claim_records import accepted_input, target_from_row
from agentos.distributed.postgres._claim_recovery import recover_expired
from agentos.distributed.postgres._claim_lifecycle import (
    heartbeat,
    release,
    ttl_seconds,
)
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.runtime.execution import AcceptedTurnExecution
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunStatus


class PostgresClaimStore:
    """Database-time execution claim and fencing adapter."""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def resolve_delivery(self, *, outbox_id: str) -> RunDeliveryTarget | None:
        async with self._database.connection() as connection:
            row = await fetchone(
                connection,
                """
                SELECT o.outbox_id, o.tenant_id, o.principal_id, o.session_id,
                       r.run_id, r.status, r.wait_kind, r.wait_handle,
                       r.wait_detail, r.wait_not_before, r.aggregate_version
                FROM agentos_distributed_outbox AS o
                JOIN agentos_distributed_runs AS r
                  ON r.tenant_id = o.tenant_id
                 AND r.session_id = o.session_id
                 AND r.run_id = o.run_id
                WHERE o.outbox_id = %s
                """,
                (outbox_id,),
            )
        return None if row is None else target_from_row(row)

    async def claim_pending_turn(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> ClaimedExecution | None:
        seconds = ttl_seconds(ttl)
        async with self._database.transaction() as connection:
            target_row = await fetchone(
                connection,
                """
                SELECT o.outbox_id, o.tenant_id, o.principal_id, o.session_id,
                       r.run_id, r.status, r.wait_kind, r.wait_handle,
                       r.wait_detail, r.wait_not_before, r.aggregate_version
                FROM agentos_distributed_outbox AS o
                JOIN agentos_distributed_runs AS r
                  ON r.tenant_id = o.tenant_id
                 AND r.session_id = o.session_id
                 AND r.run_id = o.run_id
                WHERE o.outbox_id = %s
                FOR UPDATE OF o, r
                """,
                (outbox_id,),
            )
            if target_row is None:
                return None
            target = target_from_row(target_row)
            if target.scope != scope:
                return None
            if target.run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                return None
            session = await fetchone(
                connection,
                """
                SELECT *, clock_timestamp() AS database_now
                FROM agentos_distributed_sessions
                WHERE tenant_id = %s AND session_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, target.session_id),
            )
            if session is None:
                raise ClaimConflictError()
            if session["active_claim_id"] is not None:
                return None
            input_row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_accepted_inputs
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                  AND status = 'accepted'
                ORDER BY accepted_at, turn_id
                FOR UPDATE
                """,
                (scope.tenant_id, target.session_id, target.run.run_id),
            )
            if input_row is None:
                return None
            if input_row["principal_id"] != scope.principal_id:
                raise ClaimConflictError()
            claim_id = f"claim_{uuid4().hex}"
            claimed_session = await fetchone(
                connection,
                """
                UPDATE agentos_distributed_sessions
                SET fencing_token = fencing_token + 1,
                    active_claim_id = %s,
                    active_claim_owner_id = %s,
                    active_claim_run_id = %s,
                    active_claim_expires_at =
                        clock_timestamp() + (%s * interval '1 second')
                WHERE tenant_id = %s AND session_id = %s
                RETURNING fencing_token, active_claim_expires_at
                """,
                (
                    claim_id,
                    owner_id,
                    target.run.run_id,
                    seconds,
                    scope.tenant_id,
                    target.session_id,
                ),
            )
            if claimed_session is None:
                raise ClaimConflictError()
            token = cast(int, claimed_session["fencing_token"])
            await connection.execute(
                """
                UPDATE agentos_distributed_accepted_inputs
                SET status = 'claimed', claim_id = %s, fencing_token = %s
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                  AND turn_id = %s AND status = 'accepted'
                """,
                (
                    claim_id,
                    token,
                    scope.tenant_id,
                    target.session_id,
                    target.run.run_id,
                    input_row["turn_id"],
                ),
            )
            claim = ExecutionClaim(
                scope.tenant_id,
                target.session_id,
                target.run.run_id,
                owner_id,
                claim_id,
                token,
                cast(datetime, claimed_session["active_claim_expires_at"]),
            )
            execution = AcceptedTurnExecution(
                input=accepted_input(input_row),
                guard=RunWriteGuard(target.run.aggregate_version, claim_id, token),
                mode=("start" if target.run.status is RunStatus.QUEUED else "recover"),
            )
            return ClaimedExecution(target, claim, execution)

    async def heartbeat(
        self,
        *,
        scope: RequestScope,
        claim: ExecutionClaim,
        ttl: timedelta,
    ) -> ExecutionClaim:
        return await heartbeat(
            self._database,
            scope=scope,
            claim=claim,
            ttl=ttl,
        )

    async def release(
        self,
        *,
        scope: RequestScope,
        claim: ExecutionClaim,
    ) -> None:
        await release(self._database, scope=scope, claim=claim)

    async def recover_expired(
        self,
        *,
        scope: RequestScope,
        limit: int,
    ) -> tuple[RunDeliveryTarget, ...]:
        return await recover_expired(
            self._database,
            scope=scope,
            limit=limit,
        )

__all__ = ["PostgresClaimStore"]
