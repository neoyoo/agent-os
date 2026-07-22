from __future__ import annotations

from collections.abc import Mapping

from agentos.distributed.internal_errors import StaleInternalSubmissionAuthorityError
from agentos.distributed.internal_models import InternalSubmissionAuthority
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import (
    AsyncConnection,
    Row,
    fetchall,
    fetchone,
)
from agentos.distributed.postgres._team_rows import (
    member_from_row,
    team_from_row,
)
from agentos.multi.team_errors import (
    TeamActiveRunConflictError,
    TeamBoundaryError,
    TeamConflictError,
    TeamMembershipError,
    TeamNotFoundError,
)
from agentos.multi.team_ports import TeamWorkspaceAuthorityPort
from agentos.multi.team_types import (
    TeamAccessContext,
    TeamMemberRecord,
    TeamRecord,
)


async def lock_internal_delivery(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    session_id: str,
    authority: InternalSubmissionAuthority,
    source_payload: Mapping[str, object],
) -> Row:
    """锁定并校验当前 Team delivery 的内部提交 authority。"""

    row = await lock_delivery_authority(
        connection,
        scope=scope,
        authority=authority,
    )
    if (
        row["target_session_id"] != session_id
        or row["team_id"] != source_payload.get("team_id")
        or row["message_id"] != source_payload.get("message_id")
        or row["recipient_agent_id"] != source_payload.get("recipient_agent_id")
        or source_payload.get("action") != "team_read_messages"
    ):
        raise StaleInternalSubmissionAuthorityError()
    return row


async def lock_delivery_authority(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    authority: InternalSubmissionAuthority,
) -> Row:
    row = await fetchone(
        connection,
        """
        SELECT *, clock_timestamp() AS database_now
        FROM agentos_team_deliveries
        WHERE tenant_id = %s AND delivery_id = %s FOR UPDATE
        """,
        (scope.tenant_id, authority.delivery_id),
    )
    if row is None:
        raise StaleInternalSubmissionAuthorityError()
    expires_at = row["claim_expires_at"]
    database_now = row["database_now"]
    if (
        row["state"] != "claimed"
        or row["claim_id"] != authority.claim_id
        or row["fencing_token"] != authority.fence
        or expires_at is None
        or database_now is None
        or expires_at <= database_now  # type: ignore[operator]
    ):
        raise StaleInternalSubmissionAuthorityError()
    return row


async def lock_active_team(
    connection: AsyncConnection,
    scope: RequestScope,
    team_id: str,
) -> Row:
    row = await fetchone(
        connection,
        """
        SELECT * FROM agentos_teams
        WHERE tenant_id = %s AND team_id = %s AND status = 'active'
        FOR UPDATE
        """,
        (scope.tenant_id, team_id),
    )
    if row is None:
        raise TeamNotFoundError()
    return row


async def lock_member(
    connection: AsyncConnection,
    scope: RequestScope,
    access: TeamAccessContext,
) -> TeamMemberRecord:
    row = await fetchone(
        connection,
        """
        SELECT * FROM agentos_team_members
        WHERE tenant_id = %s AND team_id = %s
          AND recipient_agent_id = %s AND status = 'active'
        FOR UPDATE
        """,
        (scope.tenant_id, access.team_id, access.recipient_agent_id),
    )
    if row is None:
        raise TeamMembershipError()
    member = member_from_row(row)
    if member.target_session_id != access.target_session_id:
        raise TeamMembershipError()
    return member


async def lock_bindings(
    connection: AsyncConnection,
    scope: RequestScope,
    team_id: str,
    recipient_ids: tuple[str, ...] | None,
) -> tuple[TeamMemberRecord, ...]:
    predicate = "" if recipient_ids is None else "AND recipient_agent_id = ANY(%s)"
    params: tuple[object, ...] = (scope.tenant_id, team_id)
    if recipient_ids is not None:
        params += (sorted(set(recipient_ids)),)
    rows = await fetchall(
        connection,
        f"""
        SELECT * FROM agentos_team_members
        WHERE tenant_id = %s AND team_id = %s {predicate}
        ORDER BY target_session_id FOR UPDATE
        """,
        params,
    )
    return tuple(member_from_row(row) for row in rows)


async def lock_sessions_and_reject_active_runs(
    connection: AsyncConnection,
    scope: RequestScope,
    session_ids: tuple[str, ...],
) -> None:
    await lock_sessions(connection, scope, session_ids)
    await reject_active_runs(connection, scope, session_ids)


async def lock_sessions(
    connection: AsyncConnection,
    scope: RequestScope,
    session_ids: tuple[str, ...],
) -> None:
    ordered = sorted(set(session_ids))
    if not ordered:
        return
    rows = await fetchall(
        connection,
        """
        SELECT session_id FROM agentos_distributed_sessions
        WHERE tenant_id = %s AND session_id = ANY(%s)
        ORDER BY session_id FOR UPDATE
        """,
        (scope.tenant_id, ordered),
    )
    if sorted(row["session_id"] for row in rows) != ordered:
        raise TeamConflictError()


async def reject_active_runs(
    connection: AsyncConnection,
    scope: RequestScope,
    session_ids: tuple[str, ...],
) -> None:
    ordered = sorted(set(session_ids))
    if not ordered:
        return
    active = await fetchone(
        connection,
        """
        SELECT run_id FROM agentos_distributed_runs
        WHERE tenant_id = %s AND session_id = ANY(%s)
          AND status IN ('created', 'queued', 'running', 'waiting')
        LIMIT 1
        """,
        (scope.tenant_id, ordered),
    )
    if active is not None:
        raise TeamActiveRunConflictError()


async def team_record(
    row: Row,
    scope: RequestScope,
    workspaces: TeamWorkspaceAuthorityPort,
) -> TeamRecord:
    workspace_id = row["workspace_id"]
    if workspace_id is not None and type(workspace_id) is not str:
        raise TypeError("workspace_id must be str or None")
    workspace = None
    if workspace_id is not None:
        workspace = await workspaces.resolve_team_workspace(
            scope=scope,
            workspace_id=workspace_id,
        )
        if workspace is None:
            raise TeamBoundaryError()
    return team_from_row(row, workspace)


def bound_actor(
    members: dict[str, TeamMemberRecord],
    access: TeamAccessContext,
) -> TeamMemberRecord:
    actor = members.get(access.recipient_agent_id)
    if (
        actor is None
        or actor.status != "active"
        or actor.target_session_id != access.target_session_id
    ):
        raise TeamMembershipError()
    return actor


def require_scope(scope: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")


def require_access(
    scope: RequestScope,
    access: object,
    team_id: str,
) -> None:
    require_scope(scope)
    if type(access) is not TeamAccessContext:
        raise TypeError("access must be TeamAccessContext")
    if access.tenant_id != scope.tenant_id or access.team_id != team_id:
        raise TeamMembershipError()


__all__ = [
    "bound_actor",
    "lock_active_team",
    "lock_bindings",
    "lock_delivery_authority",
    "lock_internal_delivery",
    "lock_member",
    "lock_sessions",
    "lock_sessions_and_reject_active_runs",
    "reject_active_runs",
    "require_access",
    "require_scope",
    "team_record",
]
