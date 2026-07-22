from __future__ import annotations

from datetime import timedelta

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    Row,
    fetchone,
)
from agentos.distributed.postgres._team_bootstrap import TARGET_COLUMNS
from agentos.distributed.postgres._team_rows import (
    delivery_from_row,
    message_from_row,
)
from agentos.distributed.postgres._team_values import row_datetime
from agentos.multi.team_delivery_types import (
    ClaimedTeamDelivery,
    TeamDeliveryClaim,
    TeamDeliveryTarget,
)
from agentos.multi.team_errors import (
    StaleTeamDeliveryClaimError,
    TeamConflictError,
)


async def claim_pending(
    database: PostgresPool,
    *,
    scope: RequestScope,
    outbox_id: str,
    claim_id: str,
    ttl: timedelta,
) -> ClaimedTeamDelivery | None:
    _require_scope(scope)
    require_identifier(outbox_id, "outbox_id")
    require_identifier(claim_id, "claim_id")
    seconds = _ttl_seconds(ttl)
    async with database.transaction() as connection:
        row = await _lock_target(connection, scope, outbox_id)
        if row is None:
            return None
        delivery = delivery_from_row(row)
        database_now = row_datetime(row, "database_now")
        if delivery.state in {"applied", "rejected"}:
            return None
        if (
            delivery.state == "claimed"
            and delivery.claim_expires_at is not None
            and delivery.claim_expires_at > database_now
        ):
            return None
        updated = await fetchone(
            connection,
            """
            UPDATE agentos_team_deliveries
            SET state = 'claimed', claim_id = %s,
                fencing_token = fencing_token + 1,
                claim_expires_at =
                    clock_timestamp() + (%s * interval '1 second'),
                updated_at = clock_timestamp()
            WHERE tenant_id = %s AND delivery_id = %s
            RETURNING *
            """,
            (claim_id, seconds, scope.tenant_id, delivery.delivery_id),
        )
        if updated is None:
            raise TeamConflictError()
        current = delivery_from_row(updated)
        target = TeamDeliveryTarget(
            tenant_id=scope.tenant_id,
            outbox_id=outbox_id,
            delivery=current,
            message=message_from_row(row, prefix="message_"),
        )
        claim = TeamDeliveryClaim(
            tenant_id=scope.tenant_id,
            team_id=current.team_id,
            delivery_id=current.delivery_id,
            claim_id=claim_id,
            fencing_token=current.fencing_token,
            expires_at=current.claim_expires_at,  # type: ignore[arg-type]
        )
        return ClaimedTeamDelivery(target, claim)


async def heartbeat(
    database: PostgresPool,
    *,
    scope: RequestScope,
    claim: TeamDeliveryClaim,
    ttl: timedelta,
) -> TeamDeliveryClaim:
    _require_claim_scope(scope, claim)
    seconds = _ttl_seconds(ttl)
    async with database.transaction() as connection:
        row = await fetchone(
            connection,
            """
            UPDATE agentos_team_deliveries
            SET claim_expires_at =
                    clock_timestamp() + (%s * interval '1 second'),
                updated_at = clock_timestamp()
            WHERE tenant_id = %s AND team_id = %s AND delivery_id = %s
              AND state = 'claimed' AND claim_id = %s AND fencing_token = %s
              AND claim_expires_at > clock_timestamp()
            RETURNING claim_expires_at
            """,
            (
                seconds,
                scope.tenant_id,
                claim.team_id,
                claim.delivery_id,
                claim.claim_id,
                claim.fencing_token,
            ),
        )
        if row is None:
            raise StaleTeamDeliveryClaimError()
    return TeamDeliveryClaim(
        tenant_id=claim.tenant_id,
        team_id=claim.team_id,
        delivery_id=claim.delivery_id,
        claim_id=claim.claim_id,
        fencing_token=claim.fencing_token,
        expires_at=row_datetime(row, "claim_expires_at"),
    )


async def release(
    database: PostgresPool,
    *,
    scope: RequestScope,
    claim: TeamDeliveryClaim,
) -> None:
    _require_claim_scope(scope, claim)
    async with database.transaction() as connection:
        row = await fetchone(
            connection,
            """
            UPDATE agentos_team_deliveries
            SET state = 'pending', claim_id = NULL, claim_expires_at = NULL,
                updated_at = clock_timestamp()
            WHERE tenant_id = %s AND team_id = %s AND delivery_id = %s
              AND state = 'claimed' AND claim_id = %s AND fencing_token = %s
              AND claim_expires_at > clock_timestamp()
            RETURNING delivery_id
            """,
            (
                scope.tenant_id,
                claim.team_id,
                claim.delivery_id,
                claim.claim_id,
                claim.fencing_token,
            ),
        )
        if row is None:
            raise StaleTeamDeliveryClaimError()


async def _lock_target(
    connection: AsyncConnection,
    scope: RequestScope,
    outbox_id: str,
) -> Row | None:
    return await fetchone(
        connection,
        f"""
        SELECT {TARGET_COLUMNS}, clock_timestamp() AS database_now
        FROM agentos_distributed_outbox AS outbox
        JOIN agentos_team_deliveries AS delivery
          ON delivery.tenant_id = outbox.tenant_id
         AND delivery.delivery_id = outbox.team_delivery_id
        JOIN agentos_team_messages AS message
          ON message.tenant_id = delivery.tenant_id
         AND message.message_id = delivery.message_id
        WHERE outbox.outbox_id = %s AND outbox.tenant_id = %s
          AND outbox.topic = 'team-deliveries'
        FOR UPDATE OF delivery
        """,
        (outbox_id, scope.tenant_id),
    )


def _require_scope(scope: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")


def _require_claim_scope(scope: RequestScope, claim: object) -> None:
    _require_scope(scope)
    if type(claim) is not TeamDeliveryClaim:
        raise TypeError("claim must be TeamDeliveryClaim")
    if claim.tenant_id != scope.tenant_id:
        raise StaleTeamDeliveryClaimError()


def _ttl_seconds(ttl: timedelta) -> float:
    if type(ttl) is not timedelta or ttl <= timedelta(0):
        raise ValueError("team delivery claim ttl must be positive")
    return ttl.total_seconds()


__all__ = ["claim_pending", "heartbeat", "release"]
