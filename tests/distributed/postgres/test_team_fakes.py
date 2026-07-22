from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from agentos.distributed.models import RequestScope
from agentos.multi.team_delivery_types import TeamDelivery
from agentos.multi.team_identity import (
    team_delivery_id,
    team_delivery_source_digest,
    team_message_id,
    team_message_request_digest,
)
from agentos.multi.team_types import (
    TeamAccessContext,
    TeamMemberRecord,
    TeamMessage,
    TeamMessageRequest,
    TeamRecipient,
    TeamRecord,
)
from agentos.workspace import WorkspaceHandle


NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
SCOPE = RequestScope("tenant_1", "principal_1")


class FakeCursor:
    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    async def fetchone(self) -> Mapping[str, object] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[Mapping[str, object]]:
        return list(self._rows)


QueryHandler = Callable[
    [str, tuple[object, ...]],
    Sequence[Mapping[str, object]],
]


class FakeConnection:
    def __init__(self, handler: QueryHandler) -> None:
        self.handler = handler
        self.trace: list[tuple[str, tuple[object, ...]]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> FakeCursor:
        normalized = " ".join(query.split())
        values = tuple(params)
        self.trace.append((normalized, values))
        return FakeCursor(self.handler(normalized, values))

    def transaction(self):  # pragma: no cover - database owns test transaction
        raise AssertionError("connection.transaction must not be used directly")


class FakeDatabase:
    def __init__(self, handler: QueryHandler) -> None:
        self.connection_value = FakeConnection(handler)
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def connection(self):
        yield self.connection_value

    @asynccontextmanager
    async def transaction(self):
        try:
            yield self.connection_value
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


class FakeWorkspaceAuthority:
    def __init__(self) -> None:
        self.team_workspace = WorkspaceHandle(
            "workspace_team_1",
            "team",
            root="C:/work/team_1",
            metadata={"node": "local-only"},
        )

    async def resolve_team_workspace(
        self,
        *,
        scope: RequestScope,
        workspace_id: str,
    ) -> WorkspaceHandle | None:
        if scope.tenant_id == SCOPE.tenant_id and workspace_id == "workspace_team_1":
            return self.team_workspace
        return None

    async def resolve_target_workspace(
        self,
        *,
        scope: RequestScope,
        target_session_id: str,
    ) -> WorkspaceHandle | None:
        return WorkspaceHandle(
            f"workspace_{target_session_id}",
            "session",
            root=f"C:/work/team_1/sessions/{target_session_id}",
            parent_workspace_id="workspace_team_1",
        )


def team() -> TeamRecord:
    return TeamRecord(
        team_id="team_1",
        leader_agent_id="agent_1",
        workspace=FakeWorkspaceAuthority().team_workspace,
        created_at=NOW,
    )


def leader() -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_1",
        role="leader",
        target_session_id="session_1",
        capabilities=("coordinate",),
        created_at=NOW,
    )


def worker(
    agent_id: str = "agent_2",
    session_id: str = "session_2",
) -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id=agent_id,
        role="worker",
        target_session_id=session_id,
        capabilities=("review",),
        created_at=NOW,
    )


def access() -> TeamAccessContext:
    return TeamAccessContext("tenant_1", "team_1", "agent_1", "session_1")


def request(*, content: str = "review the plan") -> TeamMessageRequest:
    return TeamMessageRequest(
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
        message_kind="instruction",
        content=content,
        correlation_id="correlation_1",
        addressing_kind="broadcast",
        addressed_agent_id=None,
        created_at=NOW,
    )


def message_and_delivery() -> tuple[TeamMessage, TeamDelivery]:
    item = request()
    recipient = TeamRecipient("agent_2", "session_2")
    message_id = team_message_id(
        scope=SCOPE,
        team_id=item.team_id,
        sender_agent_id=item.sender_agent_id,
        operation_id=item.operation_id,
    )
    message = TeamMessage(
        message_id=message_id,
        team_id=item.team_id,
        sender_agent_id=item.sender_agent_id,
        operation_id=item.operation_id,
        message_kind=item.message_kind,
        content=item.content,
        correlation_id=item.correlation_id,
        addressing_kind=item.addressing_kind,
        addressed_agent_id=item.addressed_agent_id,
        recipient_snapshot=(recipient,),
        request_sha256=team_message_request_digest(
            scope=SCOPE,
            team_id=item.team_id,
            message_id=message_id,
            sender_agent_id=item.sender_agent_id,
            message_kind=item.message_kind,
            content=item.content,
            correlation_id=item.correlation_id,
            addressing_kind=item.addressing_kind,
            addressed_agent_id=item.addressed_agent_id,
        ),
        created_at=NOW,
    )
    delivery = TeamDelivery(
        delivery_id=team_delivery_id(
            scope=SCOPE,
            team_id=item.team_id,
            message_id=message_id,
            recipient_agent_id=recipient.recipient_agent_id,
            target_session_id=recipient.target_session_id,
        ),
        team_id=item.team_id,
        message_id=message_id,
        recipient_agent_id=recipient.recipient_agent_id,
        target_session_id=recipient.target_session_id,
        state="pending",
        fencing_token=0,
        source_sha256=team_delivery_source_digest(
            scope=SCOPE,
            team_id=item.team_id,
            message_id=message_id,
            sender_agent_id=item.sender_agent_id,
            message_kind=item.message_kind,
            content=item.content,
            correlation_id=item.correlation_id,
            addressing_kind=item.addressing_kind,
            addressed_agent_id=item.addressed_agent_id,
            recipient_agent_id=recipient.recipient_agent_id,
            target_session_id=recipient.target_session_id,
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    return message, delivery


def team_row() -> dict[str, object]:
    return {
        "tenant_id": "tenant_1",
        "team_id": "team_1",
        "status": "active",
        "leader_agent_id": "agent_1",
        "workspace_id": "workspace_team_1",
        "created_at": NOW,
        "deleted_at": None,
    }


def member_row(member: TeamMemberRecord) -> dict[str, object]:
    return {
        "tenant_id": "tenant_1",
        "team_id": member.team_id,
        "recipient_agent_id": member.recipient_agent_id,
        "role": member.role,
        "status": member.status,
        "target_session_id": member.target_session_id,
        "capabilities_json": list(member.capabilities),
        "created_at": member.created_at,
        "deleted_at": member.deleted_at,
    }


def message_row(message: TeamMessage) -> dict[str, object]:
    return {
        "tenant_id": "tenant_1",
        "message_id": message.message_id,
        "team_id": message.team_id,
        "sender_agent_id": message.sender_agent_id,
        "operation_id": message.operation_id,
        "message_kind": message.message_kind,
        "content": message.content,
        "correlation_id": message.correlation_id,
        "addressing_kind": message.addressing_kind,
        "addressed_agent_id": message.addressed_agent_id,
        "recipient_snapshot_json": [
            {
                "recipient_agent_id": item.recipient_agent_id,
                "target_session_id": item.target_session_id,
            }
            for item in message.recipient_snapshot
        ],
        "request_sha256": message.request_sha256,
        "created_at": message.created_at,
    }


def delivery_row(delivery: TeamDelivery) -> dict[str, object]:
    return {
        "tenant_id": "tenant_1",
        "delivery_id": delivery.delivery_id,
        "team_id": delivery.team_id,
        "message_id": delivery.message_id,
        "recipient_agent_id": delivery.recipient_agent_id,
        "target_session_id": delivery.target_session_id,
        "state": delivery.state,
        "claim_id": delivery.claim_id,
        "fencing_token": delivery.fencing_token,
        "claim_expires_at": delivery.claim_expires_at,
        "source_sha256": delivery.source_sha256,
        "result_kind": delivery.result_kind,
        "observed_run_id": delivery.observed_run_id,
        "observed_aggregate_version": delivery.observed_aggregate_version,
        "observed_run_status": (
            None
            if delivery.observed_run_status is None
            else delivery.observed_run_status.value
        ),
        "created_at": delivery.created_at,
        "updated_at": delivery.updated_at,
    }


def delivery_target_row(
    message: TeamMessage,
    delivery: TeamDelivery,
    *,
    outbox_id: str,
) -> dict[str, object]:
    row = delivery_row(delivery)
    row.update(
        {
            "outbox_id": outbox_id,
            "message_sender_agent_id": message.sender_agent_id,
            "message_operation_id": message.operation_id,
            "message_message_kind": message.message_kind,
            "message_content": message.content,
            "message_correlation_id": message.correlation_id,
            "message_addressing_kind": message.addressing_kind,
            "message_addressed_agent_id": message.addressed_agent_id,
            "message_recipient_snapshot_json": [
                {
                    "recipient_agent_id": item.recipient_agent_id,
                    "target_session_id": item.target_session_id,
                }
                for item in message.recipient_snapshot
            ],
            "message_request_sha256": message.request_sha256,
            "message_created_at": message.created_at,
        },
    )
    return row
