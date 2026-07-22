from __future__ import annotations

from agentos.distributed.models import RequestScope
from agentos.multi.team_errors import TeamBoundaryError
from agentos.multi.team_ports import TeamWorkspaceAuthorityPort
from agentos.workspace import (
    WorkspaceHandle,
    WorkspacePolicy,
    WorkspacePolicyError,
)


async def validate_workspace_binding(
    workspaces: TeamWorkspaceAuthorityPort,
    policy: WorkspacePolicy,
    *,
    scope: RequestScope,
    workspace_id: object,
    target_workspace: WorkspaceHandle | None,
) -> None:
    if workspace_id is None:
        if target_workspace is not None:
            raise TeamBoundaryError()
        return
    if type(workspace_id) is not str or type(target_workspace) is not WorkspaceHandle:
        raise TeamBoundaryError()
    parent = await workspaces.resolve_team_workspace(
        scope=scope,
        workspace_id=workspace_id,
    )
    if (
        type(parent) is not WorkspaceHandle
        or parent.workspace_id != workspace_id
        or target_workspace.parent_workspace_id != parent.workspace_id
    ):
        raise TeamBoundaryError()
    try:
        policy.ensure_child_workspace_allowed(parent, target_workspace)
    except WorkspacePolicyError as error:
        raise TeamBoundaryError() from error


__all__ = ["validate_workspace_binding"]
