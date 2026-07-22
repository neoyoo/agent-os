from __future__ import annotations

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import PostgresPool, fetchall, fetchone
from agentos.distributed.postgres._team_access import require_scope, team_record
from agentos.distributed.postgres._team_rows import member_from_row
from agentos.multi.team_ports import TeamWorkspaceAuthorityPort
from agentos.multi.team_types import TeamMemberRecord, TeamRecord


async def get_team(
    database: PostgresPool,
    workspaces: TeamWorkspaceAuthorityPort,
    *,
    scope: RequestScope,
    team_id: str,
) -> TeamRecord | None:
    require_scope(scope)
    require_identifier(team_id, "team_id")
    async with database.connection() as connection:
        row = await fetchone(
            connection,
            "SELECT * FROM agentos_teams WHERE tenant_id = %s AND team_id = %s",
            (scope.tenant_id, team_id),
        )
    return None if row is None else await team_record(row, scope, workspaces)


async def get_member(
    database: PostgresPool,
    *,
    scope: RequestScope,
    team_id: str,
    recipient_agent_id: str,
) -> TeamMemberRecord | None:
    require_scope(scope)
    require_identifier(team_id, "team_id")
    require_identifier(recipient_agent_id, "recipient_agent_id")
    async with database.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT * FROM agentos_team_members
            WHERE tenant_id = %s AND team_id = %s AND recipient_agent_id = %s
            """,
            (scope.tenant_id, team_id, recipient_agent_id),
        )
    return None if row is None else member_from_row(row)


async def list_members(
    database: PostgresPool,
    *,
    scope: RequestScope,
    team_id: str,
) -> tuple[TeamMemberRecord, ...]:
    require_scope(scope)
    require_identifier(team_id, "team_id")
    async with database.connection() as connection:
        rows = await fetchall(
            connection,
            """
            SELECT * FROM agentos_team_members
            WHERE tenant_id = %s AND team_id = %s
            ORDER BY recipient_agent_id
            """,
            (scope.tenant_id, team_id),
        )
    return tuple(member_from_row(row) for row in rows)


__all__ = ["get_member", "get_team", "list_members"]
