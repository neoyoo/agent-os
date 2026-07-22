from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from agentos.distributed._model_validation import normalize_utc, require_identifier
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres._state_records import advisory_lock
from agentos.distributed.postgres._team_access import (
    bound_actor,
    lock_active_team,
    lock_bindings,
    lock_member,
    lock_sessions,
    lock_sessions_and_reject_active_runs,
    reject_active_runs,
    require_access,
    require_scope,
    team_record,
)
from agentos.distributed.postgres._team_member_records import insert_member
from agentos.distributed.postgres._team_workspace import validate_workspace_binding
from agentos.multi.team_errors import (
    TeamConflictError,
    TeamMembershipError,
)
from agentos.multi.team_ports import TeamWorkspaceAuthorityPort
from agentos.multi.team_types import (
    TeamAccessContext,
    TeamMemberRecord,
    TeamRecord,
)
from agentos.workspace import WorkspacePolicy


async def create_team(
    database: PostgresPool,
    workspaces: TeamWorkspaceAuthorityPort,
    workspace_policy: WorkspacePolicy,
    *,
    scope: RequestScope,
    team: TeamRecord,
    leader: TeamMemberRecord,
) -> TeamRecord:
    require_scope(scope)
    if type(team) is not TeamRecord or type(leader) is not TeamMemberRecord:
        raise TypeError("team and leader must use canonical Team values")
    if (
        team.status != "active"
        or leader.status != "active"
        or leader.team_id != team.team_id
        or leader.recipient_agent_id != team.leader_agent_id
        or leader.role != "leader"
    ):
        raise TeamConflictError()
    target_workspace = await workspaces.resolve_target_workspace(
        scope=scope,
        target_session_id=leader.target_session_id,
    )
    await validate_workspace_binding(
        workspaces,
        workspace_policy,
        scope=scope,
        workspace_id=(None if team.workspace is None else team.workspace.workspace_id),
        target_workspace=target_workspace,
    )
    async with database.transaction() as connection:
        await advisory_lock(connection, scope.tenant_id, "team", team.team_id)
        await advisory_lock(
            connection,
            scope.tenant_id,
            "team-session",
            leader.target_session_id,
        )
        existing = await fetchone(
            connection,
            """
            SELECT team_id FROM agentos_teams
            WHERE tenant_id = %s AND team_id = %s FOR UPDATE
            """,
            (scope.tenant_id, team.team_id),
        )
        active_session = await fetchone(
            connection,
            """
            SELECT 1 AS active_session FROM agentos_team_members
            WHERE tenant_id = %s AND target_session_id = %s
              AND status = 'active' FOR UPDATE
            """,
            (scope.tenant_id, leader.target_session_id),
        )
        session = await fetchone(
            connection,
            """
            SELECT session_id FROM agentos_distributed_sessions
            WHERE tenant_id = %s AND session_id = %s FOR UPDATE
            """,
            (scope.tenant_id, leader.target_session_id),
        )
        if session is None or existing is not None or active_session is not None:
            raise TeamConflictError()
        await connection.execute(
            """
            INSERT INTO agentos_teams (
                tenant_id, team_id, status, leader_agent_id,
                workspace_id, created_at, deleted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                scope.tenant_id,
                team.team_id,
                team.status,
                team.leader_agent_id,
                None if team.workspace is None else team.workspace.workspace_id,
                team.created_at,
                team.deleted_at,
            ),
        )
        await insert_member(connection, scope, leader)
    return team


async def add_member(
    database: PostgresPool,
    workspaces: TeamWorkspaceAuthorityPort,
    workspace_policy: WorkspacePolicy,
    *,
    scope: RequestScope,
    access: TeamAccessContext,
    member: TeamMemberRecord,
) -> TeamMemberRecord:
    if type(member) is not TeamMemberRecord:
        raise TypeError("member must be TeamMemberRecord")
    require_access(scope, access, member.team_id)
    if member.status != "active" or member.role != "worker":
        raise TeamConflictError()
    target_workspace = await workspaces.resolve_target_workspace(
        scope=scope,
        target_session_id=member.target_session_id,
    )
    async with database.transaction() as connection:
        await advisory_lock(
            connection,
            scope.tenant_id,
            "team-session",
            member.target_session_id,
        )
        await advisory_lock(
            connection,
            scope.tenant_id,
            "team-member",
            member.team_id,
            member.recipient_agent_id,
        )
        team = await lock_active_team(connection, scope, member.team_id)
        await validate_workspace_binding(
            workspaces,
            workspace_policy,
            scope=scope,
            workspace_id=team["workspace_id"],
            target_workspace=target_workspace,
        )
        actor = await lock_member(connection, scope, access)
        if actor.role != "leader":
            raise TeamMembershipError()
        existing = await fetchone(
            connection,
            """
            SELECT recipient_agent_id FROM agentos_team_members
            WHERE tenant_id = %s AND team_id = %s AND recipient_agent_id = %s
            FOR UPDATE
            """,
            (scope.tenant_id, member.team_id, member.recipient_agent_id),
        )
        active_session = await fetchone(
            connection,
            """
            SELECT 1 AS active_session FROM agentos_team_members
            WHERE tenant_id = %s AND target_session_id = %s
              AND status = 'active' FOR UPDATE
            """,
            (scope.tenant_id, member.target_session_id),
        )
        session = await fetchone(
            connection,
            """
            SELECT session_id FROM agentos_distributed_sessions
            WHERE tenant_id = %s AND session_id = %s FOR UPDATE
            """,
            (scope.tenant_id, member.target_session_id),
        )
        if existing is not None or active_session is not None or session is None:
            raise TeamConflictError()
        await insert_member(connection, scope, member)
    return member


async def remove_member(
    database: PostgresPool,
    *,
    scope: RequestScope,
    access: TeamAccessContext,
    recipient_agent_id: str,
    deleted_at: datetime,
) -> TeamMemberRecord:
    require_access(scope, access, access.team_id)
    require_identifier(recipient_agent_id, "recipient_agent_id")
    deleted_at = normalize_utc(deleted_at, "deleted_at")
    async with database.transaction() as connection:
        await lock_active_team(connection, scope, access.team_id)
        rows = await lock_bindings(
            connection,
            scope,
            access.team_id,
            (access.recipient_agent_id, recipient_agent_id),
        )
        members = {member.recipient_agent_id: member for member in rows}
        actor = bound_actor(members, access)
        member = members.get(recipient_agent_id)
        if (
            actor.role != "leader"
            or member is None
            or member.status != "active"
            or member.role != "worker"
        ):
            raise TeamMembershipError()
        await lock_sessions(
            connection,
            scope,
            tuple(item.target_session_id for item in rows),
        )
        await reject_active_runs(connection, scope, (member.target_session_id,))
        updated = replace(member, status="deleted", deleted_at=deleted_at)
        await connection.execute(
            """
            UPDATE agentos_team_members
            SET status = 'deleted', deleted_at = %s
            WHERE tenant_id = %s AND team_id = %s
              AND recipient_agent_id = %s AND status = 'active'
            """,
            (deleted_at, scope.tenant_id, access.team_id, recipient_agent_id),
        )
    return updated


async def delete_team(
    database: PostgresPool,
    workspaces: TeamWorkspaceAuthorityPort,
    *,
    scope: RequestScope,
    access: TeamAccessContext,
    deleted_at: datetime,
) -> TeamRecord:
    require_access(scope, access, access.team_id)
    deleted_at = normalize_utc(deleted_at, "deleted_at")
    async with database.transaction() as connection:
        row = await lock_active_team(connection, scope, access.team_id)
        members = await lock_bindings(connection, scope, access.team_id, None)
        actor = bound_actor(
            {member.recipient_agent_id: member for member in members},
            access,
        )
        if actor.role != "leader":
            raise TeamMembershipError()
        sessions = tuple(
            member.target_session_id
            for member in members
            if member.status == "active"
        )
        await lock_sessions_and_reject_active_runs(connection, scope, sessions)
        current = await team_record(row, scope, workspaces)
        deleted = replace(current, status="deleted", deleted_at=deleted_at)
        await connection.execute(
            """
            UPDATE agentos_team_members
            SET status = 'deleted', deleted_at = %s
            WHERE tenant_id = %s AND team_id = %s AND status = 'active'
            """,
            (deleted_at, scope.tenant_id, access.team_id),
        )
        await connection.execute(
            """
            UPDATE agentos_teams
            SET status = 'deleted', deleted_at = %s
            WHERE tenant_id = %s AND team_id = %s AND status = 'active'
            """,
            (deleted_at, scope.tenant_id, access.team_id),
        )
    return deleted


__all__ = [
    "add_member",
    "create_team",
    "delete_team",
    "remove_member",
]
