from __future__ import annotations

from pathlib import Path

import pytest

from agentos.multi import AgentInbox
from agentos.multi.serializers import (
    envelope_from_dict,
    envelope_to_dict,
    team_message_from_dict,
    team_message_to_dict,
)
from agentos.multi.team import (
    InMemoryTeamStore,
    InMemoryTeamUiStreamStore,
    InMemoryTeamWorkerSessionProvider,
    TeamMemberRecord,
    TeamMessage,
    TeamWorkerPermissionError,
    TeamWorkerPermissionPolicy,
)
from agentos.multi.team import TeamRecord
from agentos.multi.team import TeamNoticeStore, TeamRuntime
from agentos.multi.types import AgentEnvelope, TaskRequest
from agentos.workspace import WorkspaceHandle
from agentos.workspace import LocalWorkspaceProvider, WorkspaceRequest


def test_in_memory_team_store_creates_team_and_members() -> None:
    store = InMemoryTeamStore()
    team = TeamRecord(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        created_at=1.0,
    )
    leader = TeamMemberRecord(
        team_id="team_1",
        agent_id="leader",
        role="leader",
        session_id="session_leader",
        created_at=1.0,
    )
    worker = TeamMemberRecord(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        capabilities=("research",),
        session_id="session_worker",
        created_at=2.0,
    )

    store.create_team(team)
    store.add_member(leader)
    store.add_member(worker)

    assert store.get_team("team_1") == team
    assert store.get_member("team_1", "leader") == leader
    assert store.get_member("team_1", "worker") == worker
    assert store.list_members("team_1") == [leader, worker]


def test_team_runtime_sends_directed_message_and_wakeup_notice() -> None:
    store = InMemoryTeamStore()
    inbox = AgentInbox()
    notice_store = TeamNoticeStore()
    runtime = TeamRuntime(
        store=store,
        message_queue=inbox,
        notice_store=notice_store,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        leader_session_id="session_leader",
    )
    runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        session_id="session_worker",
        capabilities=("research",),
    )

    message = runtime.say(
        team_id="team_1",
        from_agent_id="leader",
        content="Find source A.",
        to_agent_id="worker",
        kind="instruction",
    )

    assert message.message_id == "team_message_1"
    assert runtime.messages_for("worker", "team_1") == [message]
    assert runtime.messages_for("leader", "team_1") == []
    deliveries = inbox.collect("worker")
    assert deliveries[0].envelope.type == "team_message"
    assert deliveries[0].envelope.payload == message
    assert notice_store.provider_for("worker").consume_notices() == (
        "Team team_1 received message team_message_1. "
        "Call read_team_messages to inspect it.",
    )


def test_team_runtime_broadcasts_to_other_members_without_sender_echo() -> None:
    store = InMemoryTeamStore()
    inbox = AgentInbox()
    runtime = TeamRuntime(
        store=store,
        message_queue=inbox,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
    )
    runtime.add_member(team_id="team_1", agent_id="worker_a", role="worker")
    runtime.add_member(team_id="team_1", agent_id="worker_b", role="worker")

    message = runtime.say(
        team_id="team_1",
        from_agent_id="worker_a",
        content="I found a source.",
        kind="observation",
    )

    assert runtime.messages_for("leader", "team_1") == [message]
    assert runtime.messages_for("worker_b", "team_1") == [message]
    assert runtime.messages_for("worker_a", "team_1") == []
    assert len(inbox.collect("leader")) == 1
    assert len(inbox.collect("worker_b")) == 1
    assert inbox.collect("worker_a") == []


def test_team_runtime_publishes_ui_events_for_lifecycle_and_messages() -> None:
    ui_stream = InMemoryTeamUiStreamStore()
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        ui_stream=ui_stream,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    team = runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        leader_session_id="session_leader",
    )
    member = runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        session_id="session_worker",
        capabilities=("research",),
    )
    message = runtime.say(
        team_id="team_1",
        from_agent_id="leader",
        to_agent_id="worker",
        content="Find source A.",
        kind="instruction",
    )
    runtime.delete_team("team_1")

    events = ui_stream.list_events("team_1")
    assert [event.kind for event in events] == [
        "team_created",
        "member_added",
        "message_appended",
        "team_deleted",
    ]
    assert events[0].payload["team_id"] == team.team_id
    assert events[0].payload["leader_agent_id"] == "leader"
    assert events[1].payload["agent_id"] == member.agent_id
    assert events[1].payload["role"] == "worker"
    assert events[2].payload["message_id"] == message.message_id
    assert events[2].payload["to_agent_id"] == "worker"
    assert events[3].payload == {"team_id": "team_1", "deleted": True}


def test_team_runtime_does_not_publish_ui_delete_event_for_missing_team() -> None:
    ui_stream = InMemoryTeamUiStreamStore()
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        ui_stream=ui_stream,
        clock=lambda: 10.0,
    )

    assert runtime.delete_team("missing_team") is False
    assert ui_stream.list_events("missing_team") == ()


def test_team_runtime_adds_member_with_narrowed_workspace(tmp_path: Path) -> None:
    workspace_provider = LocalWorkspaceProvider(base_dir=tmp_path)
    team_workspace = workspace_provider.resolve_workspace(
        WorkspaceRequest(team_id="team_1", requested_scope="team"),
    )
    worker_workspace = workspace_provider.narrow_workspace(
        team_workspace,
        child_id="worker",
        scope="task",
    )
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        workspace=team_workspace,
    )
    member = runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        workspace=worker_workspace,
    )

    assert member.workspace == worker_workspace
    assert member.workspace.parent_workspace_id == team_workspace.workspace_id


def test_team_runtime_creates_worker_session_when_provider_is_configured(
    tmp_path: Path,
) -> None:
    workspace_provider = LocalWorkspaceProvider(base_dir=tmp_path)
    team_workspace = workspace_provider.resolve_workspace(
        WorkspaceRequest(team_id="team_1", requested_scope="team"),
    )
    worker_workspace = workspace_provider.narrow_workspace(
        team_workspace,
        child_id="worker",
        scope="task",
    )
    session_provider = InMemoryTeamWorkerSessionProvider(
        id_factory=lambda request: f"session_for_{request.agent_id}",
    )
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        workspace=team_workspace,
    )
    member = runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        capabilities=("research",),
        workspace=worker_workspace,
    )

    session = session_provider.get_session("team_1", "worker")
    assert member.session_id == "session_for_worker"
    assert member.workspace == worker_workspace
    assert session is not None
    assert session.session_id == "session_for_worker"
    assert session.workspace == worker_workspace
    assert session.capabilities == ("research",)
    assert session.status == "created"


def test_team_worker_session_defaults_to_team_workspace(tmp_path: Path) -> None:
    workspace_provider = LocalWorkspaceProvider(base_dir=tmp_path)
    team_workspace = workspace_provider.resolve_workspace(
        WorkspaceRequest(team_id="team_1", requested_scope="team"),
    )
    session_provider = InMemoryTeamWorkerSessionProvider(
        id_factory=lambda request: f"session_for_{request.agent_id}",
    )
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        workspace=team_workspace,
    )
    member = runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
    )

    session = session_provider.get_session("team_1", "worker")
    assert member.workspace == team_workspace
    assert session is not None
    assert session.workspace == team_workspace


def test_team_worker_session_rejects_broader_workspace_scope(
    tmp_path: Path,
) -> None:
    workspace_provider = LocalWorkspaceProvider(base_dir=tmp_path)
    team_workspace = workspace_provider.resolve_workspace(
        WorkspaceRequest(team_id="team_1", requested_scope="team"),
    )
    wider_workspace = workspace_provider.resolve_workspace(
        WorkspaceRequest(user_id="user_1", requested_scope="user"),
    )
    session_provider = InMemoryTeamWorkerSessionProvider()
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        workspace=team_workspace,
    )

    with pytest.raises(TeamWorkerPermissionError, match="cannot broaden"):
        runtime.add_member(
            team_id="team_1",
            agent_id="worker",
            role="worker",
            workspace=wider_workspace,
        )

    assert runtime.store.get_member("team_1", "worker") is None
    assert session_provider.get_session("team_1", "worker") is None


def test_team_worker_session_rejects_workspace_root_outside_team(
    tmp_path: Path,
) -> None:
    team_workspace = WorkspaceHandle(
        workspace_id="team:team_1",
        scope="team",
        root=str(tmp_path / "team"),
    )
    outside_workspace = WorkspaceHandle(
        workspace_id="task:worker",
        scope="task",
        root=str(tmp_path / "outside" / "worker"),
        parent_workspace_id=team_workspace.workspace_id,
    )
    session_provider = InMemoryTeamWorkerSessionProvider()
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        workspace=team_workspace,
    )

    with pytest.raises(TeamWorkerPermissionError, match="outside team workspace"):
        runtime.add_member(
            team_id="team_1",
            agent_id="worker",
            role="worker",
            workspace=outside_workspace,
        )

    assert runtime.store.get_member("team_1", "worker") is None
    assert session_provider.get_session("team_1", "worker") is None


def test_team_worker_session_rejects_requested_workspace_without_team_boundary(
    tmp_path: Path,
) -> None:
    workspace_provider = LocalWorkspaceProvider(base_dir=tmp_path)
    worker_workspace = workspace_provider.resolve_workspace(
        WorkspaceRequest(task_id="task_1", requested_scope="task"),
    )
    session_provider = InMemoryTeamWorkerSessionProvider()
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
    )

    with pytest.raises(TeamWorkerPermissionError, match="team workspace is required"):
        runtime.add_member(
            team_id="team_1",
            agent_id="worker",
            role="worker",
            workspace=worker_workspace,
        )

    assert runtime.store.get_member("team_1", "worker") is None
    assert session_provider.get_session("team_1", "worker") is None


def test_team_worker_session_rejects_unapproved_capabilities() -> None:
    session_provider = InMemoryTeamWorkerSessionProvider(
        permission_policy=TeamWorkerPermissionPolicy(
            allowed_capabilities=frozenset({"research"}),
        ),
    )
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
    )

    with pytest.raises(TeamWorkerPermissionError, match="capabilities not allowed"):
        runtime.add_member(
            team_id="team_1",
            agent_id="worker",
            role="worker",
            capabilities=("research", "admin"),
        )

    assert runtime.store.get_member("team_1", "worker") is None
    assert session_provider.get_session("team_1", "worker") is None


def test_team_runtime_honors_explicit_worker_session_id() -> None:
    session_provider = InMemoryTeamWorkerSessionProvider()
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
    )

    member = runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        session_id="provided_session",
    )

    assert member.session_id == "provided_session"
    assert session_provider.get_session("team_1", "worker").session_id == (
        "provided_session"
    )


def test_team_runtime_does_not_create_worker_session_for_leader_member() -> None:
    session_provider = InMemoryTeamWorkerSessionProvider()
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
    )

    member = runtime.add_member(
        team_id="team_1",
        agent_id="co_leader",
        role="leader",
        session_id="leader_session",
    )

    assert member.session_id == "leader_session"
    assert session_provider.list_sessions("team_1") == []


def test_team_runtime_closes_worker_sessions_when_team_is_deleted() -> None:
    session_provider = InMemoryTeamWorkerSessionProvider()
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
    )
    runtime.add_member(team_id="team_1", agent_id="worker", role="worker")

    assert runtime.delete_team("team_1") is True

    session = session_provider.get_session("team_1", "worker")
    assert session is not None
    assert session.status == "closed"
    assert session.closed_at == 1.0


def test_team_message_serializes_and_round_trips_in_envelope() -> None:
    message = TeamMessage(
        message_id="msg_1",
        team_id="team_1",
        from_agent_id="worker",
        to_agent_id="leader",
        content="I found evidence.",
        kind="result",
        created_at=3.0,
        correlation_id="task_1",
        artifact_handles=("artifact://one",),
        metadata={"confidence": "high"},
    )
    envelope = AgentEnvelope(
        envelope_id="env_1",
        from_agent_id="worker",
        to_agent_id="leader",
        type="team_message",
        payload=message,
        created_at=3.0,
        correlation_id="msg_1",
    )

    assert team_message_from_dict(team_message_to_dict(message)) == message
    assert envelope_from_dict(envelope_to_dict(envelope)) == envelope


def test_team_messages_for_does_not_drain_task_envelopes() -> None:
    store = InMemoryTeamStore()
    inbox = AgentInbox()
    runtime = TeamRuntime(
        store=store,
        message_queue=inbox,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
    )
    runtime.add_member(team_id="team_1", agent_id="worker", role="worker")
    inbox.send(
        AgentEnvelope(
            envelope_id="env_task_1",
            from_agent_id="leader",
            to_agent_id="worker",
            type="task_request",
            payload=TaskRequest(task_id="task_1", instruction="Do work"),
            created_at=11.0,
            correlation_id="task_1",
        ),
    )

    assert runtime.messages_for("worker", "team_1") == []
    deliveries = inbox.collect("worker")

    assert [delivery.envelope.type for delivery in deliveries] == ["task_request"]
