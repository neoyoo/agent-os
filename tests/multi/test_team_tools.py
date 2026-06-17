from __future__ import annotations

import json

import pytest

from agentos.capabilities import ToolRegistry
from agentos.multi import AgentInbox
from agentos.multi.team import (
    AllowAllTeamToolAuthorizationPolicy,
    InMemoryTeamWorkerSessionProvider,
    InMemoryTeamStore,
    TeamToolAuthorizationError,
    TeamMembershipError,
    TeamRuntime,
    TeamTools,
)


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


def test_team_tools_default_policy_denies_management_tools() -> None:
    runtime = make_runtime()
    registry = ToolRegistry()
    TeamTools(runtime=runtime, owner_agent_id="leader").register(registry)

    with pytest.raises(TeamToolAuthorizationError, match="team_create"):
        registry.get("team_create").handler(
            {
                "team_id": "team_1",
                "name": "Research Team",
                "description": "Finds evidence.",
            },
        )
    with pytest.raises(TeamToolAuthorizationError, match="agent_create"):
        registry.get("agent_create").handler(
            {
                "team_id": "team_1",
                "agent_id": "worker",
            },
        )
    with pytest.raises(TeamToolAuthorizationError, match="team_delete"):
        registry.get("team_delete").handler({"team_id": "team_1"})


def test_team_tools_create_agent_say_and_read_flow() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    worker_tools = register_tools(runtime, "worker")

    created = json.loads(
        leader_tools.get("team_create").handler(
            {
                "team_id": "team_1",
                "name": "Research Team",
                "description": "Finds evidence.",
                "leader_session_id": "session_leader",
            },
        ),
    )
    member = json.loads(
        leader_tools.get("agent_create").handler(
            {
                "team_id": "team_1",
                "agent_id": "worker",
                "session_id": "session_worker",
                "capabilities": ["research"],
            },
        ),
    )
    sent = json.loads(
        leader_tools.get("team_say").handler(
            {
                "team_id": "team_1",
                "to_agent_id": "worker",
                "kind": "instruction",
                "content": "Find source A.",
            },
        ),
    )
    worker_messages = json.loads(
        worker_tools.get("team_read_messages").handler(
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
    leader_tools.get("team_create").handler(
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )

    member = json.loads(
        leader_tools.get("agent_create").handler(
            {
                "team_id": "team_1",
                "agent_id": "worker",
                "capabilities": ["research"],
            },
        ),
    )

    assert member["session_id"] == "session_for_worker"


def test_team_tools_say_ignores_spoofed_sender() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    leader_tools.get("team_create").handler(
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )
    leader_tools.get("agent_create").handler(
        {
            "team_id": "team_1",
            "agent_id": "worker",
        },
    )

    sent = json.loads(
        leader_tools.get("team_say").handler(
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
    leader_tools.get("team_create").handler(
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )
    leader_tools.get("agent_create").handler(
        {
            "team_id": "team_1",
            "agent_id": "worker",
        },
    )
    leader_tools.get("team_say").handler(
        {
            "team_id": "team_1",
            "to_agent_id": "worker",
            "content": "Private instruction.",
        },
    )

    leader_messages = json.loads(
        leader_tools.get("team_read_messages").handler({"team_id": "team_1"}),
    )
    worker_messages = json.loads(
        worker_tools.get("team_read_messages").handler({"team_id": "team_1"}),
    )

    assert leader_messages["messages"] == []
    assert len(worker_messages["messages"]) == 1


def test_team_tools_delete_requires_owner_membership() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    outsider_tools = register_tools(runtime, "outsider")
    leader_tools.get("team_create").handler(
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )

    with pytest.raises(TeamMembershipError):
        outsider_tools.get("team_delete").handler({"team_id": "team_1"})

    deleted = json.loads(
        leader_tools.get("team_delete").handler({"team_id": "team_1"}),
    )
    assert deleted == {"deleted": True, "team_id": "team_1"}


def test_team_tools_management_actions_require_leader_role() -> None:
    runtime = make_runtime()
    leader_tools = register_tools(runtime, "leader")
    worker_tools = register_tools(runtime, "worker")
    leader_tools.get("team_create").handler(
        {
            "team_id": "team_1",
            "name": "Research Team",
            "description": "Finds evidence.",
        },
    )
    leader_tools.get("agent_create").handler(
        {
            "team_id": "team_1",
            "agent_id": "worker",
        },
    )

    with pytest.raises(TeamMembershipError, match="leader"):
        worker_tools.get("agent_create").handler(
            {
                "team_id": "team_1",
                "agent_id": "worker_2",
            },
        )
    with pytest.raises(TeamMembershipError, match="leader"):
        worker_tools.get("team_delete").handler({"team_id": "team_1"})

    member = json.loads(
        leader_tools.get("agent_create").handler(
            {
                "team_id": "team_1",
                "agent_id": "worker_2",
            },
        ),
    )
    deleted = json.loads(
        leader_tools.get("team_delete").handler({"team_id": "team_1"}),
    )

    assert member["agent_id"] == "worker_2"
    assert deleted == {"deleted": True, "team_id": "team_1"}
