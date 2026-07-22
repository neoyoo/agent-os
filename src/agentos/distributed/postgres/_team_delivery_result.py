from __future__ import annotations

from datetime import datetime

from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    fetchone,
)
from agentos.distributed.postgres._team_rows import (
    canonical_json,
    delivery_from_row,
    result_payload,
)
from agentos.distributed.postgres._team_values import row_datetime
from agentos.multi.team_delivery_types import (
    TeamDelivery,
    TeamDeliveryClaim,
    TeamDeliveryResult,
)
from agentos.multi.team_errors import (
    StaleTeamDeliveryClaimError,
    TeamConflictError,
)
from agentos.multi.team_identity import team_outbox_id


TEAM_EVENT_TOPIC = "team-events"


async def commit_result(
    database: PostgresPool,
    *,
    scope: RequestScope,
    claim: TeamDeliveryClaim,
    result: TeamDeliveryResult,
) -> TeamDelivery:
    _require_claim_scope(scope, claim)
    if type(result) is not TeamDeliveryResult:
        raise TypeError("result must be TeamDeliveryResult")
    async with database.transaction() as connection:
        row = await fetchone(
            connection,
            """
            SELECT *, clock_timestamp() AS database_now
            FROM agentos_team_deliveries
            WHERE tenant_id = %s AND delivery_id = %s FOR UPDATE
            """,
            (scope.tenant_id, claim.delivery_id),
        )
        if row is None:
            raise StaleTeamDeliveryClaimError()
        delivery = delivery_from_row(row)
        if not _claim_is_current(delivery, claim, row_datetime(row, "database_now")):
            raise StaleTeamDeliveryClaimError()
        await _validate_result_evidence(connection, scope, delivery, result)
        state = (
            "applied"
            if result.result_kind in {"internal_start", "wakeup"}
            else "rejected"
        )
        updated = await fetchone(
            connection,
            """
            UPDATE agentos_team_deliveries
            SET state = %s, claim_id = NULL, claim_expires_at = NULL,
                result_kind = %s, observed_run_id = %s,
                observed_aggregate_version = %s, observed_run_status = %s,
                updated_at = clock_timestamp()
            WHERE tenant_id = %s AND delivery_id = %s
              AND state = 'claimed' AND claim_id = %s AND fencing_token = %s
              AND claim_expires_at > clock_timestamp()
            RETURNING *
            """,
            (
                state,
                result.result_kind,
                result.observed_run_id,
                result.observed_aggregate_version,
                (
                    None
                    if result.observed_run_status is None
                    else result.observed_run_status.value
                ),
                scope.tenant_id,
                delivery.delivery_id,
                claim.claim_id,
                claim.fencing_token,
            ),
        )
        if updated is None:
            raise StaleTeamDeliveryClaimError()
        current = delivery_from_row(updated)
        event_kind = (
            "delivery_applied" if state == "applied" else "delivery_rejected"
        )
        event = await fetchone(
            connection,
            """
            INSERT INTO agentos_team_events (
                tenant_id, team_id, delivery_id, event_kind, payload_json
            ) VALUES (%s, %s, %s, %s, %s::jsonb)
            RETURNING event_sequence, created_at
            """,
            (
                scope.tenant_id,
                delivery.team_id,
                delivery.delivery_id,
                event_kind,
                canonical_json(result_payload(delivery.delivery_id, result)),
            ),
        )
        if event is None:
            raise TeamConflictError()
        outbox_id = team_outbox_id(
            scope=scope,
            delivery_id=delivery.delivery_id,
            outbox_kind="delivery_result",
        )
        await connection.execute(
            """
            INSERT INTO agentos_distributed_outbox (
                outbox_id, tenant_id, principal_id, session_id, run_id, topic,
                payload, created_at, team_delivery_id
            ) VALUES (%s, %s, %s, %s, NULL, %s, %s::jsonb, %s, %s)
            """,
            (
                outbox_id,
                scope.tenant_id,
                scope.principal_id,
                delivery.target_session_id,
                TEAM_EVENT_TOPIC,
                canonical_json({"outbox_id": outbox_id}),
                row_datetime(event, "created_at"),
                delivery.delivery_id,
            ),
        )
    return current


async def _validate_result_evidence(
    connection: AsyncConnection,
    scope: RequestScope,
    delivery: TeamDelivery,
    result: TeamDeliveryResult,
) -> None:
    if result.result_kind == "rejected_binding_revoked":
        binding = await fetchone(
            connection,
            """
            SELECT status FROM agentos_team_members
            WHERE tenant_id = %s AND team_id = %s
              AND recipient_agent_id = %s AND target_session_id = %s
            FOR UPDATE
            """,
            (
                scope.tenant_id,
                delivery.team_id,
                delivery.recipient_agent_id,
                delivery.target_session_id,
            ),
        )
        if binding is not None and binding["status"] == "active":
            raise TeamConflictError()
        return
    if result.result_kind != "rejected_nonterminal":
        return
    run = await fetchone(
        connection,
        """
        SELECT run_id, aggregate_version, status
        FROM agentos_distributed_runs
        WHERE tenant_id = %s AND session_id = %s
          AND status IN ('created', 'queued', 'running', 'waiting')
        FOR UPDATE
        """,
        (scope.tenant_id, delivery.target_session_id),
    )
    expected_status = (
        None
        if result.observed_run_status is None
        else result.observed_run_status.value
    )
    if (
        run is None
        or run["run_id"] != result.observed_run_id
        or run["aggregate_version"] != result.observed_aggregate_version
        or run["status"] != expected_status
    ):
        raise TeamConflictError()


def _claim_is_current(
    delivery: TeamDelivery,
    claim: TeamDeliveryClaim,
    database_now: datetime,
) -> bool:
    return (
        delivery.team_id == claim.team_id
        and delivery.delivery_id == claim.delivery_id
        and delivery.state == "claimed"
        and delivery.claim_id == claim.claim_id
        and delivery.fencing_token == claim.fencing_token
        and delivery.claim_expires_at is not None
        and delivery.claim_expires_at > database_now
    )


def _require_claim_scope(scope: object, claim: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    if type(claim) is not TeamDeliveryClaim:
        raise TypeError("claim must be TeamDeliveryClaim")
    if claim.tenant_id != scope.tenant_id:
        raise StaleTeamDeliveryClaimError()


__all__ = ["TEAM_EVENT_TOPIC", "commit_result"]
