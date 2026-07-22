from __future__ import annotations

from datetime import datetime, timedelta

from agentos.distributed.internal_models import (
    InternalRunInputReceipt,
    InternalRunSubmission,
    InternalSubmissionAuthority,
)
from agentos.distributed.models import RequestScope, RunSubmissionReceipt
from agentos.distributed.postgres import _commands as commands
from agentos.distributed.postgres import _team_bootstrap as bootstrap
from agentos.distributed.postgres import _team_delivery as delivery
from agentos.distributed.postgres import _team_delivery_result as delivery_result
from agentos.distributed.postgres import _team_internal as internal
from agentos.distributed.postgres import _team_catalog as catalog
from agentos.distributed.postgres import _team_membership as membership
from agentos.distributed.postgres import _team_messages as messages
from agentos.distributed.postgres import _team_run_inputs as run_inputs
from agentos.distributed.postgres._database import PostgresPool
from agentos.multi.team_delivery_types import (
    ClaimedTeamDelivery,
    TeamDelivery,
    TeamDeliveryClaim,
    TeamDeliveryResult,
    TeamDeliveryTarget,
    TeamMessageReceipt,
)
from agentos.multi.team_event_types import TeamEventTarget
from agentos.multi.team_ports import TeamWorkspaceAuthorityPort
from agentos.multi.team_types import (
    TeamAccessContext,
    TeamMemberRecord,
    TeamMessagePage,
    TeamMessageRequest,
    TeamRecord,
)
from agentos.workspace import WorkspacePolicy
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand


class PostgresTeamStore:
    """PostgreSQL Team truth、delivery fence 与 typed event adapter。"""

    def __init__(
        self,
        database: PostgresPool,
        workspace_authority: TeamWorkspaceAuthorityPort,
        workspace_policy: WorkspacePolicy | None = None,
    ) -> None:
        self._database = database
        self._workspace_authority = workspace_authority
        self._workspace_policy = workspace_policy or WorkspacePolicy()

    async def create_team(
        self,
        *,
        scope: RequestScope,
        team: TeamRecord,
        leader: TeamMemberRecord,
    ) -> TeamRecord:
        return await membership.create_team(
            self._database,
            self._workspace_authority,
            self._workspace_policy,
            scope=scope,
            team=team,
            leader=leader,
        )

    async def get_team(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> TeamRecord | None:
        return await catalog.get_team(
            self._database,
            self._workspace_authority,
            scope=scope,
            team_id=team_id,
        )

    async def get_member(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        recipient_agent_id: str,
    ) -> TeamMemberRecord | None:
        return await catalog.get_member(
            self._database,
            scope=scope,
            team_id=team_id,
            recipient_agent_id=recipient_agent_id,
        )

    async def list_members(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> tuple[TeamMemberRecord, ...]:
        return await catalog.list_members(
            self._database,
            scope=scope,
            team_id=team_id,
        )

    async def add_member(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        member: TeamMemberRecord,
    ) -> TeamMemberRecord:
        return await membership.add_member(
            self._database,
            self._workspace_authority,
            self._workspace_policy,
            scope=scope,
            access=access,
            member=member,
        )

    async def remove_member(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        recipient_agent_id: str,
        deleted_at: datetime,
    ) -> TeamMemberRecord:
        return await membership.remove_member(
            self._database,
            scope=scope,
            access=access,
            recipient_agent_id=recipient_agent_id,
            deleted_at=deleted_at,
        )

    async def send_message(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        request: TeamMessageRequest,
    ) -> TeamMessageReceipt:
        return await messages.send_message(
            self._database,
            scope=scope,
            access=access,
            request=request,
        )

    async def list_messages(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        after_message_id: str | None = None,
        limit: int,
    ) -> TeamMessagePage:
        return await messages.list_messages(
            self._database,
            scope=scope,
            access=access,
            after_message_id=after_message_id,
            limit=limit,
        )

    async def delete_team(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        deleted_at: datetime,
    ) -> TeamRecord:
        return await membership.delete_team(
            self._database,
            self._workspace_authority,
            scope=scope,
            access=access,
            deleted_at=deleted_at,
        )

    async def resolve_delivery(
        self,
        *,
        outbox_id: str,
    ) -> TeamDeliveryTarget | None:
        return await bootstrap.resolve_delivery(self._database, outbox_id=outbox_id)

    async def claim_pending(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        claim_id: str,
        ttl: timedelta,
    ) -> ClaimedTeamDelivery | None:
        return await delivery.claim_pending(
            self._database,
            scope=scope,
            outbox_id=outbox_id,
            claim_id=claim_id,
            ttl=ttl,
        )

    async def heartbeat(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
        ttl: timedelta,
    ) -> TeamDeliveryClaim:
        return await delivery.heartbeat(
            self._database,
            scope=scope,
            claim=claim,
            ttl=ttl,
        )

    async def release(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
    ) -> None:
        await delivery.release(self._database, scope=scope, claim=claim)

    async def commit_result(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
        result: TeamDeliveryResult,
    ) -> TeamDelivery:
        return await delivery_result.commit_result(
            self._database,
            scope=scope,
            claim=claim,
            result=result,
        )

    async def resolve_event(
        self,
        *,
        outbox_id: str,
    ) -> TeamEventTarget | None:
        return await bootstrap.resolve_event(self._database, outbox_id=outbox_id)

    async def submit_internal(
        self,
        *,
        scope: RequestScope,
        submission: InternalRunSubmission,
        authority: InternalSubmissionAuthority,
    ) -> RunSubmissionReceipt:
        return await internal.submit_internal(
            self._database,
            scope=scope,
            submission=submission,
            authority=authority,
        )

    async def submit_wakeup(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
        authority: InternalSubmissionAuthority,
    ) -> DurableCommandReceipt:
        return await commands.submit_team_wakeup(
            self._database,
            scope=scope,
            session_id=session_id,
            command=command,
            authority=authority,
        )

    async def get_applied_input(
        self,
        *,
        scope: RequestScope,
        authority: InternalSubmissionAuthority,
    ) -> InternalRunInputReceipt | None:
        return await run_inputs.get_applied_input(
            self._database,
            scope=scope,
            authority=authority,
        )


__all__ = ["PostgresTeamStore"]
