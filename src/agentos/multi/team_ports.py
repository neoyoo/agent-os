from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from agentos.distributed.models import RequestScope
from agentos.multi.team_delivery_types import (
    ClaimedTeamDelivery,
    TeamDelivery,
    TeamDeliveryClaim,
    TeamDeliveryResult,
    TeamDeliveryTarget,
    TeamMessageReceipt,
)
from agentos.multi.team_event_types import (
    TeamEventEnvelope,
    TeamEventReplayBatch,
    TeamEventReplayItem,
    TeamEventTarget,
    TeamStreamGap,
)
from agentos.multi.team_types import (
    TeamAccessContext,
    TeamMemberRecord,
    TeamMessagePage,
    TeamMessageRequest,
    TeamRecord,
)
from agentos.workspace.models import WorkspaceHandle


class TeamApplicationPort(Protocol):
    """Team truth 的 tenant-scoped 原子 Application 边界。"""

    async def create_team(
        self,
        *,
        scope: RequestScope,
        team: TeamRecord,
        leader: TeamMemberRecord,
    ) -> TeamRecord: ...

    async def get_team(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> TeamRecord | None: ...

    async def get_member(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        recipient_agent_id: str,
    ) -> TeamMemberRecord | None: ...

    async def list_members(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> tuple[TeamMemberRecord, ...]: ...

    async def add_member(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        member: TeamMemberRecord,
    ) -> TeamMemberRecord: ...

    async def remove_member(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        recipient_agent_id: str,
        deleted_at: datetime,
    ) -> TeamMemberRecord: ...

    async def send_message(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        request: TeamMessageRequest,
    ) -> TeamMessageReceipt: ...

    async def list_messages(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        after_message_id: str | None = None,
        limit: int,
    ) -> TeamMessagePage: ...

    async def delete_team(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        deleted_at: datetime,
    ) -> TeamRecord: ...


class TeamDeliveryBootstrapPort(Protocol):
    """以 opaque outbox ID 解析 PostgreSQL 权威 scope 的内部边界。"""

    async def resolve_delivery(
        self,
        *,
        outbox_id: str,
    ) -> TeamDeliveryTarget | None: ...


class TeamWorkspaceAuthorityPort(Protocol):
    """从可信 Session authority 解析 Team member workspace。"""

    async def resolve_team_workspace(
        self,
        *,
        scope: RequestScope,
        workspace_id: str,
    ) -> WorkspaceHandle | None: ...

    async def resolve_target_workspace(
        self,
        *,
        scope: RequestScope,
        target_session_id: str,
    ) -> WorkspaceHandle | None: ...


class TeamEventBootstrapPort(Protocol):
    """以 opaque result outbox ID 解析 PostgreSQL 权威 event 的内部边界。"""

    async def resolve_event(
        self,
        *,
        outbox_id: str,
    ) -> TeamEventTarget | None: ...


class TeamDeliveryPort(Protocol):
    """Team delivery claim、fence 与 result 的 PostgreSQL 原子边界。"""

    async def claim_pending(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        claim_id: str,
        ttl: timedelta,
    ) -> ClaimedTeamDelivery | None: ...

    async def heartbeat(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
        ttl: timedelta,
    ) -> TeamDeliveryClaim: ...

    async def release(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
    ) -> None: ...

    async def commit_result(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
        result: TeamDeliveryResult,
    ) -> TeamDelivery: ...


class TeamEventSubscription(Protocol):
    """由 consumer 显式关闭的 Team replay + tail subscription。"""

    def __aiter__(self) -> TeamEventSubscription: ...

    async def __anext__(self) -> TeamEventReplayItem | TeamStreamGap: ...

    async def aclose(self) -> None: ...


class TeamEventReplayPort(Protocol):
    """Team event 独立于 Run event 的有界 replay + tail 边界。"""

    async def append(
        self,
        *,
        scope: RequestScope,
        event: TeamEventEnvelope,
    ) -> TeamEventReplayItem: ...

    async def replay(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        after: str | None,
        limit: int,
    ) -> TeamEventReplayBatch | TeamStreamGap: ...

    async def high_water(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> str | None: ...

    def follow(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        after: str | None,
    ) -> TeamEventSubscription: ...

    async def close(self) -> None: ...


__all__ = [
    "TeamApplicationPort",
    "TeamDeliveryBootstrapPort",
    "TeamDeliveryPort",
    "TeamEventBootstrapPort",
    "TeamEventReplayPort",
    "TeamEventSubscription",
    "TeamWorkspaceAuthorityPort",
]
