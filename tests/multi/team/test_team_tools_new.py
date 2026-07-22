from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime
import pytest

from agentos.capabilities import (
    SideEffectPolicy,
    ToolInvocation,
    ToolInvocationContext,
    ToolRegistry,
)
from agentos.distributed.models import RequestScope
from agentos.multi.team_delivery_types import TeamDelivery, TeamMessageReceipt
from agentos.multi.team_errors import TeamBoundaryError, TeamToolAuthorizationError
from agentos.multi.team_tools import (
    AllowAllTeamToolAuthorizationPolicy,
    DefaultTeamToolAuthorizationPolicy,
    TeamToolAuthorizationPolicy,
    TeamToolAuthorizationRequest,
    TeamTools,
)
from agentos.multi.team_types import (
    MAX_TEAM_MEMBER_CAPABILITIES,
    TEAM_MESSAGE_PAGE_LIMIT,
    TeamAccessContext,
    TeamMemberRecord,
    TeamMessage,
    TeamMessagePage,
    TeamRecipient,
    TeamRecord,
)
from agentos.workspace import WorkspaceHandle


NOW = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
SCOPE = RequestScope("tenant_1", "principal_1")
WORKSPACE = WorkspaceHandle(
    workspace_id="workspace_1",
    scope="session",
    root=None,
    metadata={"session_id": "session_leader"},
)


class FakeTeamRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.messages: tuple[TeamMessage, ...] = ()

    async def create_team(self, **kwargs: object) -> TeamRecord:
        self.calls.append(("create_team", kwargs))
        return TeamRecord(
            team_id=str(kwargs["team_id"]),
            leader_agent_id=str(kwargs["leader_agent_id"]),
            workspace=kwargs["workspace"],  # type: ignore[arg-type]
            created_at=NOW,
        )

    async def add_worker(self, **kwargs: object) -> TeamMemberRecord:
        self.calls.append(("add_worker", kwargs))
        return TeamMemberRecord(
            team_id=str(kwargs["team_id"]),
            recipient_agent_id=str(kwargs["recipient_agent_id"]),
            role="worker",
            target_session_id=str(kwargs["target_session_id"]),
            capabilities=kwargs["capabilities"],  # type: ignore[arg-type]
            created_at=NOW,
        )

    async def say(self, **kwargs: object) -> TeamMessageReceipt:
        self.calls.append(("say", kwargs))
        access = kwargs["access"]
        assert type(access) is TeamAccessContext
        recipient = str(kwargs.get("addressed_agent_id") or "agent_worker")
        message = TeamMessage(
            message_id="team_msg_" + "1" * 64,
            team_id=str(kwargs["team_id"]),
            sender_agent_id=access.recipient_agent_id,
            operation_id=str(kwargs["operation_id"]),
            message_kind=kwargs["message_kind"],  # type: ignore[arg-type]
            content=str(kwargs["content"]),
            correlation_id=kwargs["correlation_id"],  # type: ignore[arg-type]
            addressing_kind=kwargs["addressing_kind"],  # type: ignore[arg-type]
            addressed_agent_id=kwargs["addressed_agent_id"],  # type: ignore[arg-type]
            recipient_snapshot=(TeamRecipient(recipient, "session_worker"),),
            request_sha256="2" * 64,
            created_at=NOW,
        )
        delivery = TeamDelivery(
            delivery_id="team_delivery_" + "3" * 64,
            team_id=message.team_id,
            message_id=message.message_id,
            recipient_agent_id=recipient,
            target_session_id="session_worker",
            state="pending",
            fencing_token=0,
            source_sha256="4" * 64,
            created_at=NOW,
            updated_at=NOW,
        )
        self.messages = (message,)
        return TeamMessageReceipt(message, (delivery,), False)

    async def read_messages(self, **kwargs: object) -> TeamMessagePage:
        self.calls.append(("read_messages", kwargs))
        return TeamMessagePage(self.messages, None)

    async def delete_team(self, **kwargs: object) -> TeamRecord:
        self.calls.append(("delete_team", kwargs))
        access = kwargs["access"]
        assert type(access) is TeamAccessContext
        return TeamRecord(
            team_id=str(kwargs["team_id"]),
            leader_agent_id=access.recipient_agent_id,
            workspace=WORKSPACE,
            created_at=NOW,
            status="deleted",
            deleted_at=NOW,
        )


class FakeWorkspaceAuthority:
    async def resolve_target_workspace(self, **kwargs: object) -> None:
        return None

    async def resolve_team_workspace(self, **kwargs: object) -> None:
        return None


def _invocation(
    tool_name: str,
    arguments: dict[str, object],
    *,
    tenant_id: str | None = "tenant_1",
    session_id: str = "session_leader",
) -> ToolInvocation:
    return ToolInvocation(
        tool_name,
        arguments,
        ToolInvocationContext(
            invocation_id="invocation_" + "0" * 32,
            operation_id="operation_" + "1" * 32,
            tenant_id=tenant_id,
            session_id=session_id,
            run_id="run_1",
            turn_id="turn_1",
            tool_call_id=f"call_{tool_name}",
            attempt=1,
        ),
    )


def _tools(
    runtime: FakeTeamRuntime,
    *,
    allow_management: bool = True,
) -> ToolRegistry:
    registry = ToolRegistry()
    TeamTools(
        runtime=runtime,  # type: ignore[arg-type]
        scope=SCOPE,
        owner_agent_id="agent_leader",
        owner_session_id="session_leader",
        owner_workspace=WORKSPACE,
        owner_capabilities=("write", "read"),
        authorization_policy=(
            AllowAllTeamToolAuthorizationPolicy() if allow_management else None
        ),
        target_workspace_resolver=FakeWorkspaceAuthority(),  # type: ignore[arg-type]
    ).register(registry)
    return registry


def test_team_tools_register_frozen_schemas_and_execution_contracts() -> None:
    registry = _tools(FakeTeamRuntime())
    names = (
        "team_create",
        "agent_create",
        "team_say",
        "team_read_messages",
        "team_delete",
    )

    assert tuple(registry.get(name).name for name in names) == names
    assert tuple(registry.get(name).side_effect_policy for name in names) == (
        SideEffectPolicy.NON_RETRYABLE,
        SideEffectPolicy.NON_RETRYABLE,
        SideEffectPolicy.NON_RETRYABLE,
        SideEffectPolicy.PURE,
        SideEffectPolicy.NON_RETRYABLE,
    )
    assert all(inspect.iscoroutinefunction(registry.get(name).handler) for name in names)
    for name in names:
        schema = registry.get(name).parameters
        assert schema["additionalProperties"] is False
        properties = schema["properties"]
        assert isinstance(properties, dict | type(schema))
        assert not {
            "sender_agent_id",
            "message_id",
            "artifact_handles",
            "metadata",
            "operation_id",
        }.intersection(properties)
    read_limit = registry.get("team_read_messages").parameters["properties"]["limit"]
    assert read_limit == {
        "type": "integer",
        "minimum": 1,
        "maximum": TEAM_MESSAGE_PAGE_LIMIT,
    }


def test_team_tools_public_types_have_chinese_docstrings() -> None:
    for value in (
        AllowAllTeamToolAuthorizationPolicy,
        DefaultTeamToolAuthorizationPolicy,
        TeamToolAuthorizationPolicy,
        TeamToolAuthorizationRequest,
        TeamTools,
    ):
        docstring = inspect.getdoc(value)
        assert docstring is not None
        assert any("\u4e00" <= char <= "\u9fff" for char in docstring)


@pytest.mark.parametrize(
    "resource",
    (
        {"tool_name": "agent_create"},
        {
            "tool_name": "agent_create",
            "recipient_agent_id": "agent_worker",
            "target_session_id": "session_worker",
            "addressing_kind": "broadcast",
        },
        {"tool_name": "team_say"},
        {
            "tool_name": "team_say",
            "addressing_kind": "broadcast",
            "target_session_id": "session_worker",
        },
        {"tool_name": "team_read_messages", "capabilities": ("read",)},
    ),
)
def test_authorization_request_enforces_tool_specific_resource_shape(
    resource: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="resource intent"):
        TeamToolAuthorizationRequest(
            scope=SCOPE,
            owner_agent_id="agent_leader",
            owner_session_id="session_leader",
            team_id="team_1",
            **resource,  # type: ignore[arg-type]
        )


def test_team_tools_default_policy_denies_management_before_runtime() -> None:
    runtime = FakeTeamRuntime()
    registry = _tools(runtime, allow_management=False)

    for name, arguments in (
        ("team_create", {"team_id": "team_1"}),
        (
            "agent_create",
            {
                "team_id": "team_1",
                "recipient_agent_id": "agent_worker",
                "target_session_id": "session_worker",
            },
        ),
        ("team_delete", {"team_id": "team_1"}),
    ):
        with pytest.raises(TeamToolAuthorizationError):
            asyncio.run(registry.get(name).handler(_invocation(name, arguments)))

    assert runtime.calls == []


def test_agent_create_requires_workspace_authority_resolver() -> None:
    runtime = FakeTeamRuntime()
    registry = ToolRegistry()
    TeamTools(
        runtime=runtime,  # type: ignore[arg-type]
        scope=SCOPE,
        owner_agent_id="agent_leader",
        owner_session_id="session_leader",
        authorization_policy=AllowAllTeamToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(TeamBoundaryError):
        asyncio.run(
            registry.get("agent_create").handler(
                _invocation(
                    "agent_create",
                    {
                        "team_id": "team_1",
                        "recipient_agent_id": "agent_worker",
                        "target_session_id": "session_worker",
                    },
                ),
            ),
        )

    assert runtime.calls == []


@pytest.mark.parametrize(
    ("capabilities", "match"),
    (
        (["read", "read"], "duplicate"),
        (["read"] * (MAX_TEAM_MEMBER_CAPABILITIES + 1), "more than 32"),
    ),
)
def test_agent_create_rejects_invalid_capability_collection_before_runtime(
    capabilities: list[str],
    match: str,
) -> None:
    runtime = FakeTeamRuntime()
    tool = _tools(runtime).get("agent_create")

    with pytest.raises(ValueError, match=match):
        asyncio.run(
            tool.handler(
                _invocation(
                    "agent_create",
                    {
                        "team_id": "team_1",
                        "recipient_agent_id": "agent_worker",
                        "target_session_id": "session_worker",
                        "capabilities": capabilities,
                    },
                ),
            ),
        )

    assert runtime.calls == []


def test_team_create_uses_bound_owner_authority() -> None:
    runtime = FakeTeamRuntime()
    tool = _tools(runtime).get("team_create")

    payload = json.loads(
        asyncio.run(tool.handler(_invocation("team_create", {"team_id": "team_1"}))),
    )

    assert payload["created_at"] == "2026-07-22T09:00:00.000000Z"
    assert payload["workspace_id"] == "workspace_1"
    assert "workspace" not in payload
    name, call = runtime.calls[-1]
    assert name == "create_team"
    assert call == {
        "scope": SCOPE,
        "team_id": "team_1",
        "leader_agent_id": "agent_leader",
        "leader_target_session_id": "session_leader",
        "workspace": WORKSPACE,
        "leader_capabilities": ("read", "write"),
    }


def test_team_create_result_does_not_expose_workspace_path_or_metadata() -> None:
    runtime = FakeTeamRuntime()
    workspace = WorkspaceHandle(
        workspace_id="workspace_sensitive",
        scope="session",
        root="C:/private/customer-project",
        metadata={"session_id": "session_leader", "secret": "do-not-expose"},
    )
    registry = ToolRegistry()
    TeamTools(
        runtime=runtime,  # type: ignore[arg-type]
        scope=SCOPE,
        owner_agent_id="agent_leader",
        owner_session_id="session_leader",
        owner_workspace=workspace,
        authorization_policy=AllowAllTeamToolAuthorizationPolicy(),
    ).register(registry)

    result = asyncio.run(
        registry.get("team_create").handler(
            _invocation("team_create", {"team_id": "team_1"}),
        ),
    )

    assert json.loads(result)["workspace_id"] == "workspace_sensitive"
    assert "customer-project" not in result
    assert "do-not-expose" not in result


@pytest.mark.parametrize("tenant_id", [None, "tenant_2"])
def test_team_tools_fail_closed_on_invocation_tenant(
    tenant_id: str | None,
) -> None:
    runtime = FakeTeamRuntime()
    tool = _tools(runtime).get("team_read_messages")

    with pytest.raises(TeamToolAuthorizationError):
        asyncio.run(
            tool.handler(
                _invocation(
                    "team_read_messages",
                    {"team_id": "team_1"},
                    tenant_id=tenant_id,
                ),
            ),
        )

    assert runtime.calls == []


def test_team_tools_fail_closed_on_bound_workspace_session() -> None:
    runtime = FakeTeamRuntime()
    tool = _tools(runtime).get("team_read_messages")

    with pytest.raises(TeamToolAuthorizationError):
        asyncio.run(
            tool.handler(
                _invocation(
                    "team_read_messages",
                    {"team_id": "team_1"},
                    session_id="session_other",
                ),
            ),
        )

    assert runtime.calls == []


def test_agent_create_and_say_pass_only_trusted_identity() -> None:
    runtime = FakeTeamRuntime()
    registry = _tools(runtime)
    asyncio.run(
        registry.get("agent_create").handler(
            _invocation(
                "agent_create",
                {
                    "team_id": "team_1",
                    "recipient_agent_id": "agent_worker",
                    "target_session_id": "session_worker",
                    "capabilities": ["read"],
                },
            ),
        ),
    )
    result = asyncio.run(
        registry.get("team_say").handler(
            _invocation(
                "team_say",
                {
                    "team_id": "team_1",
                    "content": "检查图纸",
                    "addressing_kind": "direct",
                    "addressed_agent_id": "agent_worker",
                    "message_kind": "instruction",
                    "correlation_id": "wait_1",
                },
            ),
        ),
    )

    add_call = runtime.calls[-2][1]
    assert add_call["access"] == TeamAccessContext(
        "tenant_1",
        "team_1",
        "agent_leader",
        "session_leader",
    )
    assert add_call["capabilities"] == ("read",)
    assert add_call["target_workspace"] is None
    say_call = runtime.calls[-1][1]
    assert say_call["access"] == TeamAccessContext(
        "tenant_1",
        "team_1",
        "agent_leader",
        "session_leader",
    )
    assert say_call["operation_id"] == "operation_" + "1" * 32
    assert "检查图纸" in result
    assert "\\u68c0" not in result
    assert ": " not in result
    payload = json.loads(result)
    assert payload["accepted_recipient_count"] == 1
    for internal_field in (
        "deliveries",
        "delivery_id",
        "operation_id",
        "request_sha256",
        "source_sha256",
        "fencing_token",
        "target_session_id",
    ):
        assert internal_field not in result


def test_read_and_delete_use_bound_owner_and_utc_json() -> None:
    runtime = FakeTeamRuntime()
    registry = _tools(runtime)
    asyncio.run(
        registry.get("team_say").handler(
            _invocation(
                "team_say",
                {
                    "team_id": "team_1",
                    "content": "inspect",
                    "addressing_kind": "direct",
                    "addressed_agent_id": "agent_worker",
                },
            ),
        ),
    )

    messages = json.loads(
        asyncio.run(
            registry.get("team_read_messages").handler(
                _invocation(
                    "team_read_messages",
                    {"team_id": "team_1", "after_message_id": None},
                ),
            ),
        ),
    )
    deleted = json.loads(
        asyncio.run(
            registry.get("team_delete").handler(
                _invocation("team_delete", {"team_id": "team_1"}),
            ),
        ),
    )

    assert len(messages["messages"]) == 1
    assert messages["messages"][0]["created_at"].endswith("Z")
    assert messages["next_cursor"] is None
    assert not {
        "operation_id",
        "request_sha256",
        "recipient_snapshot",
        "target_session_id",
    }.intersection(messages["messages"][0])
    assert runtime.calls[-2][1]["access"] == TeamAccessContext(
        "tenant_1",
        "team_1",
        "agent_leader",
        "session_leader",
    )
    assert runtime.calls[-2][1]["limit"] == TEAM_MESSAGE_PAGE_LIMIT
    assert runtime.calls[-1][1]["access"] == TeamAccessContext(
        "tenant_1",
        "team_1",
        "agent_leader",
        "session_leader",
    )
    assert deleted["status"] == "deleted"
    assert deleted["deleted_at"] == "2026-07-22T09:00:00.000000Z"


def test_agent_create_authorization_receives_typed_resource_intent() -> None:
    runtime = FakeTeamRuntime()

    class RecordingPolicy:
        def __init__(self) -> None:
            self.requests: list[TeamToolAuthorizationRequest] = []

        def authorize_team_tool(self, request: TeamToolAuthorizationRequest) -> None:
            self.requests.append(request)

    policy = RecordingPolicy()
    registry = ToolRegistry()
    TeamTools(
        runtime=runtime,  # type: ignore[arg-type]
        scope=SCOPE,
        owner_agent_id="agent_leader",
        owner_session_id="session_leader",
        authorization_policy=policy,
        target_workspace_resolver=FakeWorkspaceAuthority(),  # type: ignore[arg-type]
    ).register(registry)

    asyncio.run(
        registry.get("agent_create").handler(
            _invocation(
                "agent_create",
                {
                    "team_id": "team_1",
                    "recipient_agent_id": "agent_worker",
                    "target_session_id": "session_worker",
                    "capabilities": ["read"],
                },
            ),
        ),
    )

    assert policy.requests[-1] == TeamToolAuthorizationRequest(
        tool_name="agent_create",
        scope=SCOPE,
        owner_agent_id="agent_leader",
        owner_session_id="session_leader",
        team_id="team_1",
        recipient_agent_id="agent_worker",
        target_session_id="session_worker",
        capabilities=("read",),
    )
