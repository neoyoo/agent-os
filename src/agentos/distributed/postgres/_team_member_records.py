from __future__ import annotations

from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import AsyncConnection
from agentos.distributed.postgres._team_rows import canonical_json
from agentos.multi.team_types import TeamMemberRecord


async def insert_member(
    connection: AsyncConnection,
    scope: RequestScope,
    member: TeamMemberRecord,
) -> None:
    await connection.execute(
        """
        INSERT INTO agentos_team_members (
            tenant_id, team_id, recipient_agent_id, role, status,
            target_session_id, capabilities_json, created_at, deleted_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        """,
        (
            scope.tenant_id,
            member.team_id,
            member.recipient_agent_id,
            member.role,
            member.status,
            member.target_session_id,
            canonical_json(list(member.capabilities)),
            member.created_at,
            member.deleted_at,
        ),
    )


__all__ = ["insert_member"]
