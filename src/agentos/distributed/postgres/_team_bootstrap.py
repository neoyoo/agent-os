from __future__ import annotations

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.postgres._database import PostgresPool, Row, fetchone
from agentos.distributed.postgres._team_rows import (
    delivery_from_row,
    event_from_row,
    message_from_row,
)
from agentos.distributed.postgres._team_values import row_text
from agentos.multi.team_delivery_types import TeamDeliveryTarget
from agentos.multi.team_event_types import TeamEventTarget


TARGET_COLUMNS = """
    delivery.*,
    outbox.outbox_id,
    message.sender_agent_id AS message_sender_agent_id,
    message.operation_id AS message_operation_id,
    message.message_kind AS message_message_kind,
    message.content AS message_content,
    message.correlation_id AS message_correlation_id,
    message.addressing_kind AS message_addressing_kind,
    message.addressed_agent_id AS message_addressed_agent_id,
    message.recipient_snapshot_json AS message_recipient_snapshot_json,
    message.request_sha256 AS message_request_sha256,
    message.created_at AS message_created_at
"""


async def resolve_delivery(
    database: PostgresPool,
    *,
    outbox_id: str,
) -> TeamDeliveryTarget | None:
    require_identifier(outbox_id, "outbox_id")
    async with database.connection() as connection:
        row = await fetchone(
            connection,
            f"""
            SELECT {TARGET_COLUMNS}
            FROM agentos_distributed_outbox AS outbox
            JOIN agentos_team_deliveries AS delivery
              ON delivery.tenant_id = outbox.tenant_id
             AND delivery.delivery_id = outbox.team_delivery_id
            JOIN agentos_team_messages AS message
              ON message.tenant_id = delivery.tenant_id
             AND message.message_id = delivery.message_id
            WHERE outbox.outbox_id = %s AND outbox.topic = 'team-deliveries'
            """,
            (outbox_id,),
        )
    return None if row is None else target_from_row(row)


async def resolve_event(
    database: PostgresPool,
    *,
    outbox_id: str,
) -> TeamEventTarget | None:
    require_identifier(outbox_id, "outbox_id")
    async with database.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT outbox.outbox_id, event.tenant_id, event.team_id,
                   event.event_sequence, event.event_kind,
                   event.payload_json, event.created_at
            FROM agentos_distributed_outbox AS outbox
            JOIN agentos_team_deliveries AS delivery
              ON delivery.tenant_id = outbox.tenant_id
             AND delivery.delivery_id = outbox.team_delivery_id
            JOIN agentos_team_events AS event
              ON event.tenant_id = delivery.tenant_id
             AND event.team_id = delivery.team_id
             AND event.delivery_id = delivery.delivery_id
            WHERE outbox.outbox_id = %s AND outbox.topic = 'team-events'
            """,
            (outbox_id,),
        )
    if row is None:
        return None
    return TeamEventTarget(
        tenant_id=row_text(row, "tenant_id"),
        outbox_id=row_text(row, "outbox_id"),
        event=event_from_row(row),
    )


def target_from_row(row: Row) -> TeamDeliveryTarget:
    return TeamDeliveryTarget(
        tenant_id=row_text(row, "tenant_id"),
        outbox_id=row_text(row, "outbox_id"),
        delivery=delivery_from_row(row),
        message=message_from_row(row, prefix="message_"),
    )


__all__ = [
    "TARGET_COLUMNS",
    "resolve_delivery",
    "resolve_event",
    "target_from_row",
]
