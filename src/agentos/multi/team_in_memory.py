from __future__ import annotations

from bisect import insort
from dataclasses import replace
from datetime import datetime
from threading import RLock

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import RequestScope
from agentos.multi.team_delivery_types import TeamDelivery, TeamMessageReceipt
from agentos.multi.team_errors import (
    TeamConflictError,
    TeamCursorError,
    TeamMembershipError,
    TeamNotFoundError,
)
from agentos.multi.team_identity import (
    team_delivery_id,
    team_delivery_source_digest,
    team_message_id,
    team_message_request_digest,
)
from agentos.multi.team_types import (
    TEAM_MESSAGE_PAGE_LIMIT,
    TeamAccessContext,
    TeamMemberRecord,
    TeamMessage,
    TeamMessagePage,
    TeamMessageRequest,
    TeamRecipient,
    TeamRecord,
)


_TeamKey = tuple[str, str]
_OperationKey = tuple[str, str, str]


class InMemoryTeamStore:
    """Team Application Contract 的 tenant-scoped 内存参考适配器。"""

    def __init__(self) -> None:
        self._teams: dict[_TeamKey, TeamRecord] = {}
        self._members: dict[_TeamKey, dict[str, TeamMemberRecord]] = {}
        self._messages: dict[_TeamKey, list[TeamMessage]] = {}
        self._operation_messages: dict[_OperationKey, TeamMessage] = {}
        self._deliveries: dict[tuple[str, str], tuple[TeamDelivery, ...]] = {}
        self._lock = RLock()

    async def create_team(
        self,
        *,
        scope: RequestScope,
        team: TeamRecord,
        leader: TeamMemberRecord,
    ) -> TeamRecord:
        tenant_id = _tenant_id(scope)
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
        key = (tenant_id, team.team_id)
        with self._lock:
            if key in self._teams or self._active_session_exists(
                tenant_id,
                leader.target_session_id,
            ):
                raise TeamConflictError()
            self._teams[key] = team
            self._members[key] = {leader.recipient_agent_id: leader}
            self._messages[key] = []
        return team

    async def get_team(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> TeamRecord | None:
        key = _team_key(scope, team_id)
        with self._lock:
            return self._teams.get(key)

    async def get_member(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        recipient_agent_id: str,
    ) -> TeamMemberRecord | None:
        key = _team_key(scope, team_id)
        require_identifier(recipient_agent_id, "recipient_agent_id")
        with self._lock:
            return self._members.get(key, {}).get(recipient_agent_id)

    async def list_members(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> tuple[TeamMemberRecord, ...]:
        key = _team_key(scope, team_id)
        with self._lock:
            members = self._members.get(key, {}).values()
            return tuple(sorted(members, key=lambda item: item.recipient_agent_id))

    async def add_member(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        member: TeamMemberRecord,
    ) -> TeamMemberRecord:
        tenant_id = _tenant_id(scope)
        _require_access(scope, access, member.team_id)
        if type(member) is not TeamMemberRecord:
            raise TypeError("member must be TeamMemberRecord")
        key = (tenant_id, member.team_id)
        with self._lock:
            self._require_active_team(key)
            actor = self._require_bound_member(key, access)
            if actor.role != "leader":
                raise TeamMembershipError()
            members = self._members[key]
            if (
                member.status != "active"
                or member.role != "worker"
                or member.recipient_agent_id in members
                or self._active_session_exists(
                    tenant_id,
                    member.target_session_id,
                )
            ):
                raise TeamConflictError()
            members[member.recipient_agent_id] = member
        return member

    async def remove_member(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        recipient_agent_id: str,
        deleted_at: datetime,
    ) -> TeamMemberRecord:
        _require_access(scope, access, access.team_id)
        key = _team_key(scope, access.team_id)
        require_identifier(recipient_agent_id, "recipient_agent_id")
        with self._lock:
            self._require_active_team(key)
            actor = self._require_bound_member(key, access)
            member = self._require_active_member(key, recipient_agent_id)
            if actor.role != "leader" or member.role != "worker":
                raise TeamMembershipError()
            deleted = replace(member, status="deleted", deleted_at=deleted_at)
            self._members[key][recipient_agent_id] = deleted
            return deleted

    async def send_message(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        request: TeamMessageRequest,
    ) -> TeamMessageReceipt:
        tenant_id = _tenant_id(scope)
        if type(request) is not TeamMessageRequest:
            raise TypeError("request must be TeamMessageRequest")
        _require_access(scope, access, request.team_id)
        if request.sender_agent_id != access.recipient_agent_id:
            raise TeamMembershipError()
        message_id = team_message_id(
            scope=scope,
            team_id=request.team_id,
            sender_agent_id=request.sender_agent_id,
            operation_id=request.operation_id,
        )
        request_digest = _request_digest(scope, request, message_id)
        operation_key = (tenant_id, request.team_id, request.operation_id)
        team_key = (tenant_id, request.team_id)

        with self._lock:
            self._require_active_team(team_key)
            self._require_bound_member(team_key, access)
            existing = self._operation_messages.get(operation_key)
            if existing is not None:
                if (
                    existing.message_id != message_id
                    or existing.request_sha256 != request_digest
                ):
                    raise TeamConflictError()
                return TeamMessageReceipt(
                    message=existing,
                    deliveries=self._deliveries[(tenant_id, existing.message_id)],
                    duplicate=True,
                )

            recipients = self._resolve_recipients(team_key, request)
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
                _delivery(scope, request, message_id, recipient)
                for recipient in recipients
            )
            insort(
                self._messages[team_key],
                message,
                key=lambda item: (item.created_at, item.message_id),
            )
            self._operation_messages[operation_key] = message
            self._deliveries[(tenant_id, message_id)] = deliveries
            return TeamMessageReceipt(
                message=message,
                deliveries=deliveries,
                duplicate=False,
            )

    async def list_messages(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        after_message_id: str | None = None,
        limit: int,
    ) -> TeamMessagePage:
        _require_access(scope, access, access.team_id)
        key = _team_key(scope, access.team_id)
        if after_message_id is not None:
            require_identifier(after_message_id, "after_message_id")
        if type(limit) is not int or not 1 <= limit <= TEAM_MESSAGE_PAGE_LIMIT:
            raise ValueError("limit must be between 1 and 10")
        with self._lock:
            self._require_active_team(key)
            self._require_bound_member(key, access)
            cursor_found = after_message_id is None
            selected: list[TeamMessage] = []
            for message in self._messages[key]:
                if not any(
                    recipient.recipient_agent_id == access.recipient_agent_id
                    and recipient.target_session_id == access.target_session_id
                    for recipient in message.recipient_snapshot
                ):
                    continue
                if not cursor_found:
                    cursor_found = message.message_id == after_message_id
                    continue
                selected.append(message)
                if len(selected) > limit:
                    break
            if not cursor_found:
                raise TeamCursorError()
            has_more = len(selected) > limit
            page = tuple(selected[:limit])
            next_cursor = page[-1].message_id if has_more else None
            return TeamMessagePage(page, next_cursor)

    async def delete_team(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        deleted_at: datetime,
    ) -> TeamRecord:
        _require_access(scope, access, access.team_id)
        key = _team_key(scope, access.team_id)
        with self._lock:
            team = self._require_active_team(key)
            actor = self._require_bound_member(key, access)
            if actor.role != "leader":
                raise TeamMembershipError()
            deleted_team = replace(
                team,
                status="deleted",
                deleted_at=deleted_at,
            )
            deleted_members = {
                agent_id: (
                    member
                    if member.status == "deleted"
                    else replace(
                        member,
                        status="deleted",
                        deleted_at=deleted_at,
                    )
                )
                for agent_id, member in self._members[key].items()
            }
            self._teams[key] = deleted_team
            self._members[key] = deleted_members
            return deleted_team

    def _require_active_team(self, key: _TeamKey) -> TeamRecord:
        team = self._teams.get(key)
        if team is None or team.status != "active":
            raise TeamNotFoundError()
        return team

    def _require_active_member(
        self,
        key: _TeamKey,
        recipient_agent_id: str,
    ) -> TeamMemberRecord:
        member = self._members.get(key, {}).get(recipient_agent_id)
        if member is None or member.status != "active":
            raise TeamMembershipError()
        return member

    def _active_session_exists(self, tenant_id: str, session_id: str) -> bool:
        return any(
            member.status == "active" and member.target_session_id == session_id
            for (candidate_tenant, _team_id), members in self._members.items()
            if candidate_tenant == tenant_id
            for member in members.values()
        )

    def _require_bound_member(
        self,
        key: _TeamKey,
        access: TeamAccessContext,
    ) -> TeamMemberRecord:
        member = self._require_active_member(key, access.recipient_agent_id)
        if member.target_session_id != access.target_session_id:
            raise TeamMembershipError()
        return member

    def _resolve_recipients(
        self,
        key: _TeamKey,
        request: TeamMessageRequest,
    ) -> tuple[TeamRecipient, ...]:
        members = self._members[key]
        if request.addressing_kind == "direct":
            assert request.addressed_agent_id is not None
            member = self._require_active_member(key, request.addressed_agent_id)
            return (TeamRecipient(member.recipient_agent_id, member.target_session_id),)
        recipients = tuple(
            TeamRecipient(member.recipient_agent_id, member.target_session_id)
            for member in sorted(
                members.values(),
                key=lambda item: item.recipient_agent_id,
            )
            if member.status == "active"
            and member.recipient_agent_id != request.sender_agent_id
        )
        if not recipients:
            raise TeamMembershipError()
        return recipients


def _tenant_id(scope: object) -> str:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    return scope.tenant_id


def _team_key(scope: RequestScope, team_id: str) -> _TeamKey:
    tenant_id = _tenant_id(scope)
    require_identifier(team_id, "team_id")
    return tenant_id, team_id


def _require_access(
    scope: RequestScope,
    access: object,
    team_id: str,
) -> None:
    tenant_id = _tenant_id(scope)
    if type(access) is not TeamAccessContext:
        raise TypeError("access must be TeamAccessContext")
    if access.tenant_id != tenant_id or access.team_id != team_id:
        raise TeamMembershipError()


def _request_digest(
    scope: RequestScope,
    request: TeamMessageRequest,
    message_id: str,
) -> str:
    return team_message_request_digest(
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


def _delivery(
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


__all__ = ["InMemoryTeamStore"]
