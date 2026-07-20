from __future__ import annotations

from typing import cast

from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import RequestScope, RunDeliveryTarget
from agentos.distributed.postgres._claim_records import target_from_row
from agentos.distributed.postgres._database import PostgresPool, fetchall, fetchone
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC, insert_outbox


async def recover_expired(
    database: PostgresPool,
    *,
    scope: RequestScope,
    limit: int,
) -> tuple[RunDeliveryTarget, ...]:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("claim recovery limit is invalid")
    recovered: list[RunDeliveryTarget] = []
    async with database.transaction() as connection:
        rows = await fetchall(
            connection,
            """
            SELECT s.session_id, s.active_claim_run_id AS run_id,
                   input.principal_id,
                   s.fencing_token, r.status, r.wait_kind, r.wait_handle,
                   r.wait_detail, r.wait_not_before, r.aggregate_version
            FROM agentos_distributed_sessions AS s
            JOIN agentos_distributed_runs AS r
              ON r.tenant_id = s.tenant_id
             AND r.session_id = s.session_id
              AND r.run_id = s.active_claim_run_id
            JOIN agentos_distributed_accepted_inputs AS input
              ON input.tenant_id = s.tenant_id
             AND input.session_id = s.session_id
             AND input.run_id = s.active_claim_run_id
             AND input.status = 'claimed'
             AND input.claim_id = s.active_claim_id
             AND input.fencing_token = s.fencing_token
            WHERE s.tenant_id = %s AND s.active_claim_id IS NOT NULL
              AND s.active_claim_expires_at <= clock_timestamp()
            ORDER BY s.active_claim_expires_at, s.session_id
            FOR UPDATE OF s, r SKIP LOCKED
            LIMIT %s
            """,
            (scope.tenant_id, limit),
        )
        for row in rows:
            token = cast(int, row["fencing_token"]) + 1
            accepted = await fetchone(
                connection,
                """
                UPDATE agentos_distributed_accepted_inputs
                SET status = 'accepted', claim_id = NULL, fencing_token = NULL
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                  AND status = 'claimed'
                RETURNING turn_id
                """,
                (scope.tenant_id, row["session_id"], row["run_id"]),
            )
            if accepted is None:
                raise ClaimConflictError()
            await connection.execute(
                """
                UPDATE agentos_distributed_sessions
                SET fencing_token = %s, active_claim_id = NULL,
                    active_claim_owner_id = NULL, active_claim_run_id = NULL,
                    active_claim_expires_at = NULL
                WHERE tenant_id = %s AND session_id = %s
                """,
                (token, scope.tenant_id, row["session_id"]),
            )
            source_id = f"{row['run_id']}:{token}"
            recovered_scope = RequestScope(
                scope.tenant_id,
                cast(str, row["principal_id"]),
            )
            identifier = await insert_outbox(
                connection,
                scope=recovered_scope,
                session_id=cast(str, row["session_id"]),
                run_id=cast(str, row["run_id"]),
                source_kind="recover",
                source_id=source_id,
                topic=EXECUTION_TOPIC,
                payload={"fencing_token": token},
            )
            target_row = dict(row)
            target_row.update(
                tenant_id=recovered_scope.tenant_id,
                principal_id=recovered_scope.principal_id,
                outbox_id=identifier,
            )
            recovered.append(target_from_row(target_row))
    return tuple(recovered)


__all__ = ["recover_expired"]
