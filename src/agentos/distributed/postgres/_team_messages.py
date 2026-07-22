from __future__ import annotations

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    Row,
    fetchall,
    fetchone,
)
from agentos.distributed.postgres._state_records import advisory_lock
from agentos.distributed.postgres._team_access import (
    lock_active_team,
    lock_member,
    require_access,
)
from agentos.distributed.postgres._team_message_records import (
    insert_delivery_and_outbox,
    insert_message,
    load_message_deliveries,
    new_delivery,
)
from agentos.distributed.postgres._team_rows import member_from_row, message_from_row
from agentos.multi.team_delivery_types import TeamMessageReceipt
from agentos.multi.team_errors import (
    TeamConflictError,
    TeamCursorError,
    TeamMembershipError,
)
from agentos.multi.team_identity import (
    team_message_id,
    team_message_request_digest,
)
from agentos.multi.team_types import (
    TEAM_MESSAGE_PAGE_LIMIT,
    TeamAccessContext,
    TeamMessage,
    TeamMessagePage,
    TeamMessageRequest,
    TeamRecipient,
)


async def send_message(
    database: PostgresPool,
    *,
    scope: RequestScope,
    access: TeamAccessContext,
    request: TeamMessageRequest,
) -> TeamMessageReceipt:
    if type(request) is not TeamMessageRequest:
        raise TypeError("request must be TeamMessageRequest")
    require_access(scope, access, request.team_id)
    if request.sender_agent_id != access.recipient_agent_id:
        raise TeamMembershipError()
    message_id = team_message_id(
        scope=scope,
        team_id=request.team_id,
        sender_agent_id=request.sender_agent_id,
        operation_id=request.operation_id,
    )
    request_digest = team_message_request_digest(
        scope=scope,
        team_id=request.team_id,
        message_id=message_id,
        sender_agent_id=request.sender_agent_id,
        message_kind=request.message_kind,
        content=request.content,
        correlation_id=request.correlation_id,
        addressing_kind=request.addressing_kind,
        addressed_agent_id=request.addressed_agent_id,
    )
    async with database.transaction() as connection:
        await advisory_lock(
            connection,
            scope.tenant_id,
            request.team_id,
            request.operation_id,
        )
        await lock_active_team(connection, scope, request.team_id)
        await lock_member(connection, scope, access)
        existing = await fetchone(
            connection,
            """
            SELECT * FROM agentos_team_messages
            WHERE tenant_id = %s AND team_id = %s AND operation_id = %s
            FOR UPDATE
            """,
            (scope.tenant_id, request.team_id, request.operation_id),
        )
        if existing is not None:
            message = message_from_row(existing)
            if message.message_id != message_id or message.request_sha256 != request_digest:
                raise TeamConflictError()
            deliveries = await load_message_deliveries(
                connection,
                scope,
                message.message_id,
            )
            return TeamMessageReceipt(message, deliveries, True)
        recipients = await _resolve_recipients(connection, scope, request)
        message = TeamMessage(
            message_id=message_id,
            team_id=request.team_id,
            sender_agent_id=request.sender_agent_id,
            operation_id=request.operation_id,
            message_kind=request.message_kind,
            content=request.content,
            correlation_id=request.correlation_id,
            addressing_kind=request.addressing_kind,
            addressed_agent_id=request.addressed_agent_id,
            recipient_snapshot=recipients,
            request_sha256=request_digest,
            created_at=request.created_at,
        )
        deliveries = tuple(
            new_delivery(scope, request, message_id, recipient)
            for recipient in recipients
        )
        await insert_message(connection, scope, message)
        for item in deliveries:
            await insert_delivery_and_outbox(connection, scope, item)
    return TeamMessageReceipt(message, deliveries, False)


async def list_messages(
    database: PostgresPool,
    *,
    scope: RequestScope,
    access: TeamAccessContext,
    after_message_id: str | None,
    limit: int,
) -> TeamMessagePage:
    require_access(scope, access, access.team_id)
    if after_message_id is not None:
        require_identifier(after_message_id, "after_message_id")
    if type(limit) is not int or not 1 <= limit <= TEAM_MESSAGE_PAGE_LIMIT:
        raise ValueError("limit must be between 1 and 10")
    async with database.transaction() as connection:
        await lock_active_team(connection, scope, access.team_id)
        await lock_member(connection, scope, access)
        cursor: Row | None = None
        if after_message_id is not None:
            cursor = await _visible_cursor(connection, scope, access, after_message_id)
            if cursor is None:
                raise TeamCursorError()
        rows = await _visible_messages(connection, scope, access, cursor, limit + 1)
    messages = tuple(message_from_row(row) for row in rows[:limit])
    return TeamMessagePage(
        messages,
        messages[-1].message_id if len(rows) > limit else None,
    )


async def _resolve_recipients(
    connection: AsyncConnection,
    scope: RequestScope,
    request: TeamMessageRequest,
) -> tuple[TeamRecipient, ...]:
    if request.addressing_kind == "direct":
        rows = await fetchall(
            connection,
            """
            SELECT * FROM agentos_team_members
            WHERE tenant_id = %s AND team_id = %s
              AND recipient_agent_id = %s AND status = 'active'
            FOR KEY SHARE
            """,
            (scope.tenant_id, request.team_id, request.addressed_agent_id),
        )
    else:
        rows = await fetchall(
            connection,
            """
            SELECT * FROM agentos_team_members
            WHERE tenant_id = %s AND team_id = %s
              AND recipient_agent_id <> %s AND status = 'active'
            ORDER BY recipient_agent_id FOR KEY SHARE
            """,
            (scope.tenant_id, request.team_id, request.sender_agent_id),
        )
    recipients = tuple(
        TeamRecipient(member.recipient_agent_id, member.target_session_id)
        for member in (member_from_row(row) for row in rows)
    )
    if not recipients:
        raise TeamMembershipError()
    return recipients


async def _visible_cursor(
    connection: AsyncConnection,
    scope: RequestScope,
    access: TeamAccessContext,
    message_id: str,
) -> Row | None:
    return await fetchone(
        connection,
        """
        SELECT message.message_sequence, message.message_id
        FROM agentos_team_messages AS message
        JOIN agentos_team_deliveries AS delivery
          ON delivery.tenant_id = message.tenant_id
         AND delivery.message_id = message.message_id
        WHERE message.tenant_id = %s AND message.team_id = %s
          AND message.message_id = %s
          AND delivery.recipient_agent_id = %s
          AND delivery.target_session_id = %s
        """,
        (
            scope.tenant_id,
            access.team_id,
            message_id,
            access.recipient_agent_id,
            access.target_session_id,
        ),
    )


async def _visible_messages(
    connection: AsyncConnection,
    scope: RequestScope,
    access: TeamAccessContext,
    cursor: Row | None,
    limit: int,
) -> list[Row]:
    cursor_clause = ""
    params: tuple[object, ...] = (
        scope.tenant_id,
        access.team_id,
        access.recipient_agent_id,
        access.target_session_id,
    )
    if cursor is not None:
        cursor_clause = "AND message.message_sequence > %s"
        params += (cursor["message_sequence"],)
    params += (limit,)
    return await fetchall(
        connection,
        f"""
        SELECT message.* FROM agentos_team_messages AS message
        JOIN agentos_team_deliveries AS delivery
          ON delivery.tenant_id = message.tenant_id
         AND delivery.message_id = message.message_id
        WHERE message.tenant_id = %s AND message.team_id = %s
          AND delivery.recipient_agent_id = %s
          AND delivery.target_session_id = %s
          {cursor_clause}
        ORDER BY message.message_sequence LIMIT %s
        """,
        params,
    )


__all__ = ["list_messages", "send_message"]
