from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

from agentos.distributed._execution_outcomes import CommittedExecutionOutcome
from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import (
    ClaimedExecution,
    ExecutionClaim,
    RequestScope,
    RunDeliveryTarget,
)
from agentos.distributed.postgres._claim_records import accepted_input, target_from_row
from agentos.distributed.postgres._committed_outcomes import (
    input_matches_outbox,
    resolve_committed_outcome,
)
from agentos.distributed.postgres._claim_preparation import (
    classify_accepted_turn_preparation,
)
from agentos.distributed.postgres._claim_recovery import recover_expired
from agentos.distributed.postgres._claim_lifecycle import (
    heartbeat,
    release,
    ttl_seconds,
)
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    Row,
    fetchone,
)
from agentos.runtime.execution import AcceptedTurnExecution
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunStatus


async def _delivery_target_row(
    connection: AsyncConnection,
    outbox_id: str,
    *,
    for_update: bool,
) -> Row | None:
    query = """
        SELECT o.outbox_id, o.tenant_id, o.principal_id, o.session_id,
               o.payload ->> 'kind' AS outbox_kind,
               o.payload ->> 'turn_id' AS outbox_turn_id,
               o.payload ->> 'fencing_token' AS outbox_fencing_token,
               o.payload ->> 'recovery_id' AS outbox_recovery_id,
               r.run_id, r.status, r.wait_kind, r.wait_handle,
               r.wait_detail, r.wait_not_before, r.aggregate_version
        FROM agentos_distributed_outbox AS o
        JOIN agentos_distributed_runs AS r
          ON r.tenant_id = o.tenant_id
         AND r.session_id = o.session_id
         AND r.run_id = o.run_id
        WHERE o.outbox_id = %s
          AND o.topic = 'agentos.run.execution'
    """
    if for_update:
        query += " FOR UPDATE OF o, r"
    return await fetchone(connection, query, (outbox_id,))


class PostgresClaimStore:
    """Database-time execution claim and fencing adapter."""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def resolve_delivery(self, *, outbox_id: str) -> RunDeliveryTarget | None:
        async with self._database.connection() as connection:
            row = await _delivery_target_row(
                connection,
                outbox_id,
                for_update=False,
            )
        return None if row is None else target_from_row(row)

    async def resolve_committed_outcome(
        self,
        *,
        outbox_id: str,
    ) -> CommittedExecutionOutcome | None:
        return await resolve_committed_outcome(
            self._database,
            outbox_id=outbox_id,
        )

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
            candidate_row = await _delivery_target_row(
                connection,
                outbox_id,
                for_update=False,
            )
            if candidate_row is None:
                return None
            candidate = target_from_row(candidate_row)
            if candidate.scope != scope:
                return None
            if candidate.run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                return None
            session = await fetchone(
                connection,
                """
                SELECT *, clock_timestamp() AS database_now
                FROM agentos_distributed_sessions
                WHERE tenant_id = %s AND session_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, candidate.session_id),
            )
            if session is None:
                raise ClaimConflictError()
            target_row = await _delivery_target_row(
                connection,
                outbox_id,
                for_update=True,
            )
            if target_row is None:
                return None
            target = target_from_row(target_row)
            if target.scope != scope or target.session_id != candidate.session_id:
                return None
            if target.run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                return None
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
            if not input_matches_outbox(
                input_row,
                target_row,
                tenant_id=scope.tenant_id,
                outbox_id=outbox_id,
                current_fencing_token=cast(int, session["fencing_token"]),
            ):
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
            input = accepted_input(input_row)
            guard = RunWriteGuard(target.run.aggregate_version, claim_id, token)
            preparation = await classify_accepted_turn_preparation(
                connection,
                scope=scope,
                run=target.run,
                accepted=input,
                guard=guard,
            )
            execution = AcceptedTurnExecution(
                input=input,
                guard=guard,
                preparation=preparation,
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
