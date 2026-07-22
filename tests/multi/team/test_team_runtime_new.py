from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from agentos.distributed.models import RequestScope
from agentos.multi.team_delivery_types import TeamDelivery, TeamMessageReceipt
from agentos.multi.team_errors import (
    TeamBoundaryError,
    TeamMembershipError,
)
from agentos.multi.team_ports import TeamApplicationPort
from agentos.multi.team_runtime import TeamRuntime
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
from agentos.workspace import WorkspaceHandle


NOW = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
SCOPE = RequestScope("tenant_1", "principal_1")
LEADER_ACCESS = TeamAccessContext("tenant_1", "team_1", "agent_1", "session_1")


def _port() -> AsyncMock:
    return AsyncMock(spec=TeamApplicationPort)


def _team(root: Path | None = None) -> TeamRecord:
    workspace = None
    if root is not None:
        workspace = WorkspaceHandle(
            workspace_id="workspace_1",
            scope="session",
            root=str(root),
        )
    return TeamRecord(
        team_id="team_1",
        leader_agent_id="agent_1",
        workspace=workspace,
        created_at=NOW,
    )


def _leader(*, capabilities: tuple[str, ...] = ("read", "write")) -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_1",
        role="leader",
        target_session_id="session_1",
        capabilities=capabilities,
        created_at=NOW,
    )


def test_runtime_creates_team_and_leader_in_one_port_operation() -> None:
    port = _port()
    port.create_team.side_effect = lambda **kwargs: kwargs["team"]
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    team = asyncio.run(
        runtime.create_team(
            scope=SCOPE,
            team_id="team_1",
            leader_agent_id="agent_1",
            leader_target_session_id="session_1",
            leader_capabilities=("write", "read"),
        ),
    )

    assert team.team_id == "team_1"
    call = port.create_team.await_args.kwargs
    assert call["scope"] is SCOPE
    assert call["leader"].role == "leader"
    assert call["leader"].capabilities == ("read", "write")


def test_runtime_rejects_mismatched_created_team() -> None:
    port = _port()
    port.create_team.return_value = TeamRecord(
        team_id="team_1",
        leader_agent_id="agent_1",
        workspace=None,
        created_at=NOW,
        status="deleted",
        deleted_at=NOW,
    )
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    with pytest.raises(ValueError, match="mismatched"):
        asyncio.run(
            runtime.create_team(
                scope=SCOPE,
                team_id="team_1",
                leader_agent_id="agent_1",
                leader_target_session_id="session_1",
            ),
        )


def test_runtime_adds_only_worker_with_narrowed_boundary(tmp_path: Path) -> None:
    root = tmp_path / "team"
    child = root / "tasks" / "agent_2"
    team = _team(root)
    leader = _leader()
    port = _port()
    port.get_team.return_value = team
    port.get_member.return_value = leader
    port.add_member.side_effect = lambda **kwargs: kwargs["member"]
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    member = asyncio.run(
        runtime.add_worker(
            scope=SCOPE,
            team_id="team_1",
            access=LEADER_ACCESS,
            recipient_agent_id="agent_2",
            target_session_id="session_2",
            capabilities=("read",),
            target_workspace=WorkspaceHandle(
                workspace_id="workspace_2",
                scope="task",
                root=str(child),
                parent_workspace_id="workspace_1",
            ),
        ),
    )

    assert member.role == "worker"
    assert member.target_session_id == "session_2"
    assert port.add_member.await_args.kwargs["access"] == LEADER_ACCESS


@pytest.mark.parametrize("capabilities", [("admin",), ("read", "admin")])
def test_runtime_rejects_capability_broadening_before_write(
    capabilities: tuple[str, ...],
) -> None:
    port = _port()
    port.get_team.return_value = _team()
    port.get_member.return_value = _leader(capabilities=("read",))
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    with pytest.raises(TeamBoundaryError):
        asyncio.run(
            runtime.add_worker(
                scope=SCOPE,
                team_id="team_1",
                access=LEADER_ACCESS,
                recipient_agent_id="agent_2",
                target_session_id="session_2",
                capabilities=capabilities,
            ),
        )

    port.add_member.assert_not_awaited()


def test_runtime_rejects_invalid_worker_capabilities_before_port_reads() -> None:
    port = _port()
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    with pytest.raises(ValueError, match="more than 32"):
        asyncio.run(
            runtime.add_worker(
                scope=SCOPE,
                team_id="team_1",
                access=LEADER_ACCESS,
                recipient_agent_id="agent_2",
                target_session_id="session_2",
                capabilities=("read",) * 33,
            ),
        )

    port.get_team.assert_not_awaited()
    port.get_member.assert_not_awaited()
    port.add_member.assert_not_awaited()


def test_runtime_rejects_non_leader_management_before_write() -> None:
    port = _port()
    port.get_team.return_value = _team()
    port.get_member.return_value = TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_2",
        role="worker",
        target_session_id="session_2",
        created_at=NOW,
    )
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    with pytest.raises(TeamMembershipError):
        asyncio.run(
            runtime.add_worker(
                scope=SCOPE,
                team_id="team_1",
                access=TeamAccessContext(
                    "tenant_1",
                    "team_1",
                    "agent_2",
                    "session_2",
                ),
                recipient_agent_id="agent_3",
                target_session_id="session_3",
            ),
        )

    port.add_member.assert_not_awaited()


def test_runtime_rejects_leader_from_another_target_session() -> None:
    port = _port()
    port.get_team.return_value = _team()
    port.get_member.return_value = _leader()
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    with pytest.raises(TeamMembershipError):
        asyncio.run(
            runtime.add_worker(
                scope=SCOPE,
                team_id="team_1",
                access=TeamAccessContext(
                    "tenant_1",
                    "team_1",
                    "agent_1",
                    "session_other",
                ),
                recipient_agent_id="agent_2",
                target_session_id="session_2",
            ),
        )

    port.add_member.assert_not_awaited()


def test_runtime_requires_resolved_target_workspace_for_workspace_team(
    tmp_path: Path,
) -> None:
    port = _port()
    port.get_team.return_value = _team(tmp_path / "team")
    port.get_member.return_value = _leader()
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    with pytest.raises(TeamBoundaryError):
        asyncio.run(
            runtime.add_worker(
                scope=SCOPE,
                team_id="team_1",
                access=LEADER_ACCESS,
                recipient_agent_id="agent_2",
                target_session_id="session_2",
                capabilities=("read",),
            ),
        )

    port.add_member.assert_not_awaited()


def test_runtime_removes_only_worker_binding_as_leader() -> None:
    port = _port()
    port.get_team.return_value = _team()
    worker = TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_2",
        role="worker",
        target_session_id="session_2",
        created_at=NOW,
    )
    port.get_member.side_effect = (_leader(), worker)
    deleted = TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_2",
        role="worker",
        target_session_id="session_2",
        created_at=NOW,
        status="deleted",
        deleted_at=NOW,
    )
    port.remove_member.return_value = deleted
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    result = asyncio.run(
        runtime.remove_worker(
            scope=SCOPE,
            team_id="team_1",
            access=LEADER_ACCESS,
            recipient_agent_id="agent_2",
        ),
    )

    assert result == deleted
    assert port.remove_member.await_args.kwargs["deleted_at"] == NOW


def test_runtime_say_uses_trusted_sender_and_operation_identity() -> None:
    port = _port()
    request = TeamMessageRequest(
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
        message_kind="instruction",
        content="inspect",
        correlation_id="wait_1",
        addressing_kind="direct",
        addressed_agent_id="agent_2",
        created_at=NOW,
    )
    message = TeamMessage(
        message_id="team_msg_" + "1" * 64,
        team_id=request.team_id,
        sender_agent_id=request.sender_agent_id,
        operation_id=request.operation_id,
        message_kind=request.message_kind,
        content=request.content,
        correlation_id=request.correlation_id,
        addressing_kind=request.addressing_kind,
        addressed_agent_id=request.addressed_agent_id,
        recipient_snapshot=(TeamRecipient("agent_2", "session_2"),),
        request_sha256="2" * 64,
        created_at=NOW,
    )
    delivery = TeamDelivery(
        delivery_id="team_delivery_" + "3" * 64,
        team_id="team_1",
        message_id=message.message_id,
        recipient_agent_id="agent_2",
        target_session_id="session_2",
        state="pending",
        fencing_token=0,
        source_sha256="4" * 64,
        created_at=NOW,
        updated_at=NOW,
    )
    port.send_message.return_value = TeamMessageReceipt(
        message=message,
        deliveries=(delivery,),
        duplicate=False,
    )
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    receipt = asyncio.run(
        runtime.say(
            scope=SCOPE,
            team_id="team_1",
            access=LEADER_ACCESS,
            operation_id="operation_1",
            content="inspect",
            addressing_kind="direct",
            addressed_agent_id="agent_2",
            message_kind="instruction",
            correlation_id="wait_1",
        ),
    )

    sent = port.send_message.await_args.kwargs["request"]
    assert sent.sender_agent_id == "agent_1"
    assert sent.operation_id == "operation_1"
    assert receipt.message is message


def test_runtime_delegates_scoped_read_and_delete() -> None:
    port = _port()
    page = TeamMessagePage(messages=(), next_cursor=None)
    port.list_messages.return_value = page
    deleted = TeamRecord(
        team_id="team_1",
        leader_agent_id="agent_1",
        workspace=None,
        created_at=NOW,
        status="deleted",
        deleted_at=NOW,
    )
    port.delete_team.return_value = deleted
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    messages = asyncio.run(
        runtime.read_messages(
            scope=SCOPE,
            team_id="team_1",
            access=LEADER_ACCESS,
            after_message_id=None,
        ),
    )
    result = asyncio.run(
        runtime.delete_team(
            scope=SCOPE,
            team_id="team_1",
            access=LEADER_ACCESS,
        ),
    )

    assert messages == page
    assert port.list_messages.await_args.kwargs["limit"] == TEAM_MESSAGE_PAGE_LIMIT
    assert result.status == "deleted"
    assert port.delete_team.await_args.kwargs["deleted_at"] == NOW


def test_runtime_rejects_message_page_outside_member_visibility() -> None:
    port = _port()
    message = TeamMessage(
        message_id="team_msg_" + "1" * 64,
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
        message_kind="instruction",
        content="inspect",
        correlation_id=None,
        addressing_kind="direct",
        addressed_agent_id="agent_2",
        recipient_snapshot=(TeamRecipient("agent_2", "session_2"),),
        request_sha256="2" * 64,
        created_at=NOW,
    )
    port.list_messages.return_value = TeamMessagePage((message,), None)
    runtime = TeamRuntime(port=cast(TeamApplicationPort, port), clock=lambda: NOW)

    with pytest.raises(ValueError, match="visibility"):
        asyncio.run(
            runtime.read_messages(
                scope=SCOPE,
                team_id="team_1",
                access=LEADER_ACCESS,
            ),
        )
