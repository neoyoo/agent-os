from __future__ import annotations

from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import (
    AsyncConnection,
    fetchall,
)
from agentos.distributed.postgres._team_rows import (
    canonical_json,
    delivery_from_row,
    recipient_snapshot_json,
)
from agentos.multi.team_delivery_types import TeamDelivery
from agentos.multi.team_identity import (
    team_delivery_id,
    team_delivery_source_digest,
    team_outbox_id,
)
from agentos.multi.team_types import TeamMessage, TeamMessageRequest, TeamRecipient


TEAM_DELIVERY_TOPIC = "team-deliveries"


async def load_message_deliveries(
    connection: AsyncConnection,
    scope: RequestScope,
    message_id: str,
) -> tuple[TeamDelivery, ...]:
    rows = await fetchall(
        connection,
        """
        SELECT * FROM agentos_team_deliveries
        WHERE tenant_id = %s AND message_id = %s
        ORDER BY recipient_agent_id
        """,
        (scope.tenant_id, message_id),
    )
    return tuple(delivery_from_row(row) for row in rows)


async def insert_message(
    connection: AsyncConnection,
    scope: RequestScope,
    message: TeamMessage,
) -> None:
    await connection.execute(
        """
        INSERT INTO agentos_team_messages (
            tenant_id, team_id, message_id, sender_agent_id, operation_id,
            message_kind, content, correlation_id, addressing_kind,
            addressed_agent_id, recipient_snapshot_json, request_sha256, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        """,
        (
            scope.tenant_id,
            message.team_id,
            message.message_id,
            message.sender_agent_id,
            message.operation_id,
            message.message_kind,
            message.content,
            message.correlation_id,
            message.addressing_kind,
            message.addressed_agent_id,
            recipient_snapshot_json(message.recipient_snapshot),
            message.request_sha256,
            message.created_at,
        ),
    )


async def insert_delivery_and_outbox(
    connection: AsyncConnection,
    scope: RequestScope,
    delivery: TeamDelivery,
) -> None:
    await connection.execute(
        """
        INSERT INTO agentos_team_deliveries (
            tenant_id, delivery_id, team_id, message_id, recipient_agent_id,
            target_session_id, state, fencing_token, source_sha256,
            created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            scope.tenant_id,
            delivery.delivery_id,
            delivery.team_id,
            delivery.message_id,
            delivery.recipient_agent_id,
            delivery.target_session_id,
            delivery.state,
            delivery.fencing_token,
            delivery.source_sha256,
            delivery.created_at,
            delivery.updated_at,
        ),
    )
    outbox_id = team_outbox_id(
        scope=scope,
        delivery_id=delivery.delivery_id,
        outbox_kind="delivery_ready",
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
            TEAM_DELIVERY_TOPIC,
            canonical_json({"outbox_id": outbox_id}),
            delivery.created_at,
            delivery.delivery_id,
        ),
    )


def new_delivery(
    scope: RequestScope,
    request: TeamMessageRequest,
    message_id: str,
    recipient: TeamRecipient,
) -> TeamDelivery:
    return TeamDelivery(
        delivery_id=team_delivery_id(
            scope=scope,
            team_id=request.team_id,
            message_id=message_id,
            recipient_agent_id=recipient.recipient_agent_id,
            target_session_id=recipient.target_session_id,
        ),
        team_id=request.team_id,
        message_id=message_id,
        recipient_agent_id=recipient.recipient_agent_id,
        target_session_id=recipient.target_session_id,
        state="pending",
        fencing_token=0,
        source_sha256=team_delivery_source_digest(
            scope=scope,
            team_id=request.team_id,
            message_id=message_id,
            sender_agent_id=request.sender_agent_id,
            message_kind=request.message_kind,
            content=request.content,
            correlation_id=request.correlation_id,
            addressing_kind=request.addressing_kind,
            addressed_agent_id=request.addressed_agent_id,
            recipient_agent_id=recipient.recipient_agent_id,
            target_session_id=recipient.target_session_id,
        ),
        created_at=request.created_at,
        updated_at=request.created_at,
    )


__all__ = [
    "TEAM_DELIVERY_TOPIC",
    "insert_delivery_and_outbox",
    "insert_message",
    "load_message_deliveries",
    "new_delivery",
]
