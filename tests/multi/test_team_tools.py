from __future__ import annotations

import json

import pytest

from agentos.capabilities import SideEffectPolicy, ToolRegistry
from agentos.multi import AgentInbox
from agentos.multi.team import (
    AllowAllTeamToolAuthorizationPolicy,
    InMemoryTeamWorkerSessionProvider,
    InMemoryTeamStore,
    TeamToolAuthorizationError,
    TeamMembershipError,
    TeamRuntime,
    TeamTools,
    TeamWorkerPermissionError,
    TeamWorkerPermissionPolicy,
)
from tests.tool_invocation import call_sync_tool


def make_runtime() -> TeamRuntime:
    return TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )


def register_tools(runtime: TeamRuntime, owner_agent_id: str) -> ToolRegistry:
    registry = ToolRegistry()
    TeamTools(
        runtime=runtime,
        owner_agent_id=owner_agent_id,
        authorization_policy=AllowAllTeamToolAuthorizationPolicy(),
    ).register(registry)
    return registry


def test_team_tools_register_external_tools() -> None:
    registry = register_tools(make_runtime(), "leader")

    names = [
        spec["function"]["name"]
        for spec in registry.provider_tool_specs()
    ]

    assert names == [
        "team_create",
        "agent_create",
        "team_say",
        "team_read_messages",
        "team_delete",
    ]
    assert [registry.get(name).side_effect_policy for name in names] == [
        SideEffectPolicy.NON_RETRYABLE,
        SideEffectPolicy.NON_RETRYABLE,
        SideEffectPolicy.NON_RETRYABLE,
        SideEffectPolicy.PURE,
        SideEffectPolicy.NON_RETRYABLE,
    ]


def test_team_tools_default_policy_denies_management_tools() -> None:
    runtime = make_runtime()
    registry = ToolRegistry()
    TeamTools(runtime=runtime, owner_agent_id="leader").register(registry)

    with pytest.raises(TeamToolAuthorizationError, match="team_create"):
        call_sync_tool(
            registry.get("team_create"),
            {
                "team_id": "team_1",
                "name": "Research Team",
                "description": "Finds evidence.",
            },
        )
    with pytest.raises(TeamToolAuthorizationError, match="agent_create"):
        call_sync_tool(
            registry.get("agent_create"),
            {
                "team_id": "team_1",
                "agent_id": "worker",
            },
        )
    with pytest.raises(TeamToolAuthorizationError, match="team_delete"):
        call_sync_tool(registry.get("team_delete"), {"team_id": "team_1"})


def test_team_tools_create_agent_say_and_read_flow() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    worker_tools = register_tools(runtime, "worker")

    created = json.loads(
        call_sync_tool(
            leader_tools.get("team_create"),
            {
                "team_id": "team_1",
                "name": "Research Team",
                "description": "Finds evidence.",
                "leader_session_id": "session_leader",
            },
        ),
    )
    member = json.loads(
        call_sync_tool(
            leader_tools.get("agent_create"),
            {
                "team_id": "team_1",
                "agent_id": "worker",
                "session_id": "session_worker",
                "capabilities": ["research"],
            },
        ),
    )
    sent = json.loads(
        call_sync_tool(
            leader_tools.get("team_say"),
            {
                "team_id": "team_1",
                "to_agent_id": "worker",
                "kind": "instruction",
                "content": "Find source A.",
            },
        ),
    )
    worker_messages = json.loads(
        call_sync_tool(
            worker_tools.get("team_read_messages"),
            {
                "team_id": "team_1",
            },
        ),
    )

    assert created["team"]["team_id"] == "team_1"
    assert created["leader"]["agent_id"] == "leader"
    assert member["agent_id"] == "worker"
    assert member["capabilities"] == ["research"]
    assert sent["from_agent_id"] == "leader"
    assert sent["to_agent_id"] == "worker"
    assert worker_messages["messages"] == [sent]


def test_team_tools_agent_create_returns_generated_worker_session_id() -> None:
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=InMemoryTeamWorkerSessionProvider(
            id_factory=lambda request: f"session_for_{request.agent_id}",
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    leader_tools = register_tools(runtime, "leader")
    call_sync_tool(
        leader_tools.get("team_create"),
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )

    member = json.loads(
        call_sync_tool(
            leader_tools.get("agent_create"),
            {
                "team_id": "team_1",
                "agent_id": "worker",
                "capabilities": ["research"],
            },
        ),
    )

    assert member["session_id"] == "session_for_worker"


def test_team_tools_agent_create_honors_worker_capability_allowlist() -> None:
    session_provider = InMemoryTeamWorkerSessionProvider(
        permission_policy=TeamWorkerPermissionPolicy(
            allowed_capabilities=frozenset({"research"}),
        ),
    )
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=session_provider,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    leader_tools = register_tools(runtime, "leader")
    call_sync_tool(
        leader_tools.get("team_create"),
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )

    with pytest.raises(TeamWorkerPermissionError, match="capabilities not allowed"):
        call_sync_tool(
            leader_tools.get("agent_create"),
            {
                "team_id": "team_1",
                "agent_id": "worker",
                "capabilities": ["research", "admin"],
            },
        )

    assert runtime.store.get_member("team_1", "worker") is None
    assert session_provider.get_session("team_1", "worker") is None


def test_team_tools_say_ignores_spoofed_sender() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    call_sync_tool(
        leader_tools.get("team_create"),
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )
    call_sync_tool(
        leader_tools.get("agent_create"),
        {
            "team_id": "team_1",
            "agent_id": "worker",
        },
    )

    sent = json.loads(
        call_sync_tool(
            leader_tools.get("team_say"),
            {
                "team_id": "team_1",
                "from_agent_id": "worker",
                "to_agent_id": "worker",
                "content": "Try to spoof sender.",
            },
        ),
    )

    assert sent["from_agent_id"] == "leader"


def test_team_tools_read_messages_is_scoped_to_owner_visibility() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    worker_tools = register_tools(runtime, "worker")
    call_sync_tool(
        leader_tools.get("team_create"),
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )
    call_sync_tool(
        leader_tools.get("agent_create"),
        {
            "team_id": "team_1",
            "agent_id": "worker",
        },
    )
    call_sync_tool(
        leader_tools.get("team_say"),
        {
            "team_id": "team_1",
            "to_agent_id": "worker",
            "content": "Private instruction.",
        },
    )

    leader_messages = json.loads(
        call_sync_tool(
            leader_tools.get("team_read_messages"),
            {"team_id": "team_1"},
        ),
    )
    worker_messages = json.loads(
        call_sync_tool(
            worker_tools.get("team_read_messages"),
            {"team_id": "team_1"},
        ),
    )

    assert leader_messages["messages"] == []
    assert len(worker_messages["messages"]) == 1


def test_team_tools_delete_requires_owner_membership() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    outsider_tools = register_tools(runtime, "outsider")
    call_sync_tool(
        leader_tools.get("team_create"),
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )

    with pytest.raises(TeamMembershipError):
        call_sync_tool(
            outsider_tools.get("team_delete"),
            {"team_id": "team_1"},
        )

    deleted = json.loads(
        call_sync_tool(leader_tools.get("team_delete"), {"team_id": "team_1"}),
    )
    assert deleted == {"deleted": True, "team_id": "team_1"}


def test_team_tools_management_actions_require_leader_role() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    worker_tools = register_tools(runtime, "worker")
    call_sync_tool(
        leader_tools.get("team_create"),
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )
    call_sync_tool(
        leader_tools.get("agent_create"),
        {
            "team_id": "team_1",
            "agent_id": "worker",
        },
    )

    with pytest.raises(TeamMembershipError, match="leader"):
        call_sync_tool(
            worker_tools.get("agent_create"),
            {
                "team_id": "team_1",
                "agent_id": "worker_2",
            },
        )
    with pytest.raises(TeamMembershipError, match="leader"):
        call_sync_tool(
            worker_tools.get("team_delete"),
            {"team_id": "team_1"},
        )

    member = json.loads(
        call_sync_tool(
            leader_tools.get("agent_create"),
            {
                "team_id": "team_1",
                "agent_id": "worker_2",
            },
        ),
    )
    deleted = json.loads(
        call_sync_tool(leader_tools.get("team_delete"), {"team_id": "team_1"}),
    )

    assert member["agent_id"] == "worker_2"
    assert deleted == {"deleted": True, "team_id": "team_1"}
