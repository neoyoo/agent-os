from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from agentos.distributed.models import RequestScope
from agentos.multi.team_delivery_types import TeamMessageReceipt
from agentos.multi.team_errors import (
    TeamBoundaryError,
    TeamMembershipError,
    TeamNotFoundError,
)
from agentos.multi.team_ports import TeamApplicationPort
from agentos.multi.team_types import (
    TEAM_MESSAGE_PAGE_LIMIT,
    TeamAccessContext,
    TeamAddressingKind,
    TeamMemberRecord,
    TeamMessage,
    TeamMessageKind,
    TeamMessagePage,
    TeamMessageRequest,
    TeamRecord,
)
from agentos.workspace.models import WorkspaceHandle
from agentos.workspace.policies import WorkspacePolicy, WorkspacePolicyError


class TeamRuntime:
    """协调 Team 领域操作与成员边界，不持有 delivery 或 Worker 状态。"""

    def __init__(
        self,
        *,
        port: TeamApplicationPort,
        workspace_policy: WorkspacePolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._port = port
        self._workspace_policy = workspace_policy or WorkspacePolicy()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create_team(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        leader_agent_id: str,
        leader_target_session_id: str,
        workspace: WorkspaceHandle | None = None,
        leader_capabilities: tuple[str, ...] = (),
    ) -> TeamRecord:
        """原子创建 Team 及唯一 leader binding。"""

        _require_scope(scope)
        now = self._clock()
        team = TeamRecord(
            team_id=team_id,
            leader_agent_id=leader_agent_id,
            workspace=workspace,
            created_at=now,
        )
        leader = TeamMemberRecord(
            team_id=team_id,
            recipient_agent_id=leader_agent_id,
            role="leader",
            target_session_id=leader_target_session_id,
            capabilities=leader_capabilities,
            created_at=now,
        )
        created = await self._port.create_team(scope=scope, team=team, leader=leader)
        if type(created) is not TeamRecord:
            raise TypeError("team port must return TeamRecord")
        if created != team:
            raise ValueError("team port returned a mismatched team")
        return created

    async def add_worker(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        access: TeamAccessContext,
        recipient_agent_id: str,
        target_session_id: str,
        capabilities: tuple[str, ...] = (),
        target_workspace: WorkspaceHandle | None = None,
    ) -> TeamMemberRecord:
        """在 leader authority 下添加边界不扩大的 worker binding。"""

        _require_access(scope, access, team_id)
        member = TeamMemberRecord(
            team_id=team_id,
            recipient_agent_id=recipient_agent_id,
            role="worker",
            target_session_id=target_session_id,
            capabilities=capabilities,
            created_at=self._clock(),
        )
        team = await self._active_team(scope, team_id)
        leader = await self._active_member(
            scope,
            team_id,
            access.recipient_agent_id,
        )
        if (
            leader.role != "leader"
            or team.leader_agent_id != access.recipient_agent_id
            or leader.target_session_id != access.target_session_id
        ):
            raise TeamMembershipError
        if not set(member.capabilities).issubset(leader.capabilities):
            raise TeamBoundaryError
        self.validate_member_workspace(team.workspace, target_workspace)
        created = await self._port.add_member(
            scope=scope,
            access=access,
            member=member,
        )
        if type(created) is not TeamMemberRecord:
            raise TypeError("team port must return TeamMemberRecord")
        if created != member:
            raise ValueError("team port returned a mismatched member")
        return created

    async def remove_worker(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        access: TeamAccessContext,
        recipient_agent_id: str,
    ) -> TeamMemberRecord:
        """由 leader 软删除一个 worker binding。"""

        _require_access(scope, access, team_id)
        team = await self._active_team(scope, team_id)
        leader = await self._active_member(
            scope,
            team_id,
            access.recipient_agent_id,
        )
        member = await self._active_member(scope, team_id, recipient_agent_id)
        if (
            leader.role != "leader"
            or team.leader_agent_id != access.recipient_agent_id
            or leader.target_session_id != access.target_session_id
        ):
            raise TeamMembershipError
        if member.role != "worker":
            raise TeamMembershipError
        deleted_at = self._clock()
        expected = replace(member, status="deleted", deleted_at=deleted_at)
        deleted = await self._port.remove_member(
            scope=scope,
            access=access,
            recipient_agent_id=recipient_agent_id,
            deleted_at=deleted_at,
        )
        if type(deleted) is not TeamMemberRecord:
            raise TypeError("team port must return TeamMemberRecord")
        if deleted != expected:
            raise ValueError("team port returned a mismatched deleted member")
        return deleted

    async def say(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        access: TeamAccessContext,
        operation_id: str,
        content: str,
        addressing_kind: TeamAddressingKind,
        addressed_agent_id: str | None = None,
        message_kind: TeamMessageKind = "observation",
        correlation_id: str | None = None,
    ) -> TeamMessageReceipt:
        """提交 sender-bound、幂等且原子 fanout 的 Team message。"""

        _require_access(scope, access, team_id)
        request = TeamMessageRequest(
            team_id=team_id,
            sender_agent_id=access.recipient_agent_id,
            operation_id=operation_id,
            message_kind=message_kind,
            content=content,
            correlation_id=correlation_id,
            addressing_kind=addressing_kind,
            addressed_agent_id=addressed_agent_id,
            created_at=self._clock(),
        )
        receipt = await self._port.send_message(
            scope=scope,
            access=access,
            request=request,
        )
        if type(receipt) is not TeamMessageReceipt:
            raise TypeError("team port must return TeamMessageReceipt")
        message = receipt.message
        if not _message_matches_request(message, request):
            raise ValueError("team port returned a mismatched message")
        return receipt

    async def read_messages(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        access: TeamAccessContext,
        after_message_id: str | None = None,
        limit: int = TEAM_MESSAGE_PAGE_LIMIT,
    ) -> TeamMessagePage:
        """读取当前 active member 可见的 Team message。"""

        _require_access(scope, access, team_id)
        _require_page_limit(limit)
        page = await self._port.list_messages(
            scope=scope,
            access=access,
            after_message_id=after_message_id,
            limit=limit,
        )
        if type(page) is not TeamMessagePage:
            raise TypeError("team port must return TeamMessagePage")
        if any(
            message.team_id != team_id
            or not any(
                recipient.recipient_agent_id == access.recipient_agent_id
                and recipient.target_session_id == access.target_session_id
                for recipient in message.recipient_snapshot
            )
            for message in page.messages
        ):
            raise ValueError("team port returned a message outside member visibility")
        if len(page.messages) > limit:
            raise ValueError("team port returned more messages than requested")
        return page

    async def delete_team(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        access: TeamAccessContext,
    ) -> TeamRecord:
        """由 leader 标记 Team 为 deleted。"""

        _require_access(scope, access, team_id)
        deleted = await self._port.delete_team(
            scope=scope,
            access=access,
            deleted_at=self._clock(),
        )
        if type(deleted) is not TeamRecord or deleted.team_id != team_id:
            raise TypeError("team port must return the deleted TeamRecord")
        if deleted.status != "deleted":
            raise ValueError("team port did not delete the team")
        return deleted

    async def _active_team(self, scope: RequestScope, team_id: str) -> TeamRecord:
        team = await self._port.get_team(scope=scope, team_id=team_id)
        if team is None:
            raise TeamNotFoundError
        if type(team) is not TeamRecord:
            raise TypeError("team port must return TeamRecord or None")
        if team.status != "active":
            raise TeamNotFoundError
        return team

    async def _active_member(
        self,
        scope: RequestScope,
        team_id: str,
        recipient_agent_id: str,
    ) -> TeamMemberRecord:
        member = await self._port.get_member(
            scope=scope,
            team_id=team_id,
            recipient_agent_id=recipient_agent_id,
        )
        if member is None:
            raise TeamMembershipError
        if type(member) is not TeamMemberRecord:
            raise TypeError("team port must return TeamMemberRecord or None")
        if member.status != "active":
            raise TeamMembershipError
        return member

    def validate_member_workspace(
        self,
        team_workspace: WorkspaceHandle | None,
        target_workspace: WorkspaceHandle | None,
    ) -> None:
        """校验成员 workspace 没有扩大 Team 的权限边界。"""

        if team_workspace is None:
            if target_workspace is None:
                return
            raise TeamBoundaryError
        if target_workspace is None:
            raise TeamBoundaryError
        if target_workspace.parent_workspace_id != team_workspace.workspace_id:
            raise TeamBoundaryError
        try:
            self._workspace_policy.ensure_child_workspace_allowed(
                team_workspace,
                target_workspace,
            )
        except WorkspacePolicyError as error:
            raise TeamBoundaryError from error


def _require_page_limit(limit: object) -> None:
    if type(limit) is not int or not 1 <= limit <= TEAM_MESSAGE_PAGE_LIMIT:
        raise ValueError("limit must be between 1 and 10")


def _require_scope(scope: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")


def _require_access(
    scope: object,
    access: object,
    team_id: str,
) -> None:
    _require_scope(scope)
    if type(access) is not TeamAccessContext:
        raise TypeError("access must be TeamAccessContext")
    assert isinstance(scope, RequestScope)
    if access.tenant_id != scope.tenant_id or access.team_id != team_id:
        raise TeamMembershipError


def _message_matches_request(
    message: TeamMessage,
    request: TeamMessageRequest,
) -> bool:
    return (
        message.team_id == request.team_id
        and message.sender_agent_id == request.sender_agent_id
        and message.operation_id == request.operation_id
        and message.message_kind == request.message_kind
        and message.content == request.content
        and message.correlation_id == request.correlation_id
        and message.addressing_kind == request.addressing_kind
        and message.addressed_agent_id == request.addressed_agent_id
    )


__all__ = ["TeamRuntime"]
