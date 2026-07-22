from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from agentos import AgentBuilder
from agentos._builder_distributed import ClaimScopedAgentFactory
from agentos.capabilities import ToolInvocation, ToolInvocationContext
from agentos.distributed.models import ClaimedExecution, RequestScope
from agentos.multi.team_errors import (
    TeamMembershipError,
    TeamToolAuthorizationError,
)
from agentos.multi.team_identity import team_delivery_id
from agentos.multi.team_types import (
    TeamAccessContext,
    TeamMemberRecord,
    TeamMessagePage,
    TeamRecord,
)
from agentos.providers import FakeProvider
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.execution import (
    AcceptedInternalStartInput,
    AcceptedTurnExecution,
    RestoreAcceptedTurn,
    RunExecutionCursor,
)
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from tests.distributed.worker._fakes import claimed_execution
from tests.planning._async import async_test


NOW = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)
TEAM_PAYLOAD = {
    "team_id": "team_1",
    "message_id": "team_msg_" + "1" * 64,
    "recipient_agent_id": "agent_worker",
    "action": "team_read_messages",
}
TEAM_DELIVERY_ID = team_delivery_id(
    scope=RequestScope("tenant_1", "principal_1"),
    team_id="team_1",
    message_id=TEAM_PAYLOAD["message_id"],
    recipient_agent_id="agent_worker",
    target_session_id="session_1",
)


class _BoundStateStore:
    async def load_checkpoint(self, session_id: str) -> None:
        assert session_id == "session_1"
        return None


class _StateStore:
    def bind(self, scope: RequestScope) -> _BoundStateStore:
        assert scope.tenant_id == "tenant_1"
        return _BoundStateStore()


class _ResumeValidator:
    async def validate(self, **kwargs: object) -> None:
        del kwargs


class _WorkspaceAuthority:
    def __init__(self) -> None:
        self.target_calls: list[tuple[RequestScope, str]] = []

    async def resolve_target_workspace(
        self,
        *,
        scope: RequestScope,
        target_session_id: str,
    ) -> None:
        self.target_calls.append((scope, target_session_id))
        return None

    async def resolve_team_workspace(self, **kwargs: object) -> None:
        del kwargs
        return None


class _InvalidWorkspaceAuthority(_WorkspaceAuthority):
    async def resolve_target_workspace(
        self,
        *,
        scope: RequestScope,
        target_session_id: str,
    ) -> object:
        self.target_calls.append((scope, target_session_id))
        return object()


class _Teams:
    def __init__(self, member: TeamMemberRecord | None) -> None:
        self.member = member
        self.team_calls: list[tuple[RequestScope, str]] = []
        self.member_calls: list[tuple[RequestScope, str, str]] = []
        self.read_access: list[TeamAccessContext] = []

    async def get_team(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> TeamRecord:
        self.team_calls.append((scope, team_id))
        return TeamRecord(team_id, "agent_leader", None, NOW)

    async def get_member(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        recipient_agent_id: str,
    ) -> TeamMemberRecord | None:
        self.member_calls.append((scope, team_id, recipient_agent_id))
        return self.member

    async def list_messages(
        self,
        *,
        scope: RequestScope,
        access: TeamAccessContext,
        after_message_id: str | None,
        limit: int,
    ) -> TeamMessagePage:
        del scope, after_message_id, limit
        self.read_access.append(access)
        return TeamMessagePage((), None)


def _member(*, session_id: str = "session_1") -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_worker",
        role="worker",
        target_session_id=session_id,
        capabilities=("read",),
        created_at=NOW,
    )


def _accepted_internal() -> AcceptedInternalStartInput:
    return AcceptedInternalStartInput(
        "run_1",
        "team_submission_" + "2" * 64,
        "team_message",
        TEAM_PAYLOAD,
        "turn_team",
    )


def _claimed_with(
    accepted: AcceptedInternalStartInput | AcceptedContinuationInput,
    *,
    restore: bool = False,
) -> ClaimedExecution:
    base_preparation = None
    if restore:
        base_preparation = RestoreAcceptedTurn(
            RunExecutionCursor("turn_1", "before_provider", 0),
        )
    base = claimed_execution(preparation=base_preparation)
    preparation = base.execution.preparation
    if restore:
        preparation = RestoreAcceptedTurn(
            RunExecutionCursor(accepted.turn_id, "before_provider", 0),
        )
    execution = AcceptedTurnExecution(
        accepted,
        base.execution.guard,
        preparation,
    )
    return replace(base, execution=execution)


def _factory(
    teams: _Teams,
    workspaces: _WorkspaceAuthority,
) -> ClaimScopedAgentFactory:
    return ClaimScopedAgentFactory(
        builder=AgentBuilder().provider(FakeProvider([])),
        state_store=_StateStore(),  # type: ignore[arg-type]
        artifact_store=object(),  # type: ignore[arg-type]
        side_effect_store=InMemorySideEffectStore(),
        side_effect_resume_validator=_ResumeValidator(),
        team_port=teams,  # type: ignore[arg-type]
        team_workspace_authority=workspaces,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("accepted", "restore"),
    (
        (_accepted_internal(), False),
        (_accepted_internal(), True),
        (
            AcceptedContinuationInput(
                "run_1",
                "team_command_" + "3" * 64,
                "wakeup",
                TEAM_PAYLOAD,
                "turn_team_wakeup",
                team_delivery_id=TEAM_DELIVERY_ID,
            ),
            False,
        ),
    ),
)
@async_test
async def test_team_authority_is_rebuilt_for_every_worker_claim(
    accepted: AcceptedInternalStartInput | AcceptedContinuationInput,
    restore: bool,
) -> None:
    teams = _Teams(_member())
    workspaces = _WorkspaceAuthority()
    factory = _factory(teams, workspaces)
    claimed = _claimed_with(accepted, restore=restore)

    first = await factory.hydrate(claimed=claimed)
    second = await factory.hydrate(claimed=claimed)

    expected_scope = RequestScope("tenant_1", "principal_1")
    assert teams.team_calls == [(expected_scope, "team_1")] * 2
    assert teams.member_calls == [
        (expected_scope, "team_1", "agent_worker"),
    ] * 2
    assert workspaces.target_calls == [(expected_scope, "session_1")] * 2
    assert first.query_loop.tool_call_router.tool_registry.get(
        "team_read_messages",
    ) is not second.query_loop.tool_call_router.tool_registry.get(
        "team_read_messages",
    )
    assert factory.builder._tools is None


@async_test
async def test_claim_scoped_team_tool_uses_binding_owner_and_rejects_another_team() -> None:
    teams = _Teams(_member())
    factory = _factory(teams, _WorkspaceAuthority())
    agent = await factory.hydrate(claimed=_claimed_with(_accepted_internal()))
    tool = agent.query_loop.tool_call_router.tool_registry.get("team_read_messages")

    result = tool.handler(_invocation("team_1"))
    assert await cast(Awaitable[str], result) == '{"messages":[],"next_cursor":null}'
    assert teams.read_access == [
        TeamAccessContext("tenant_1", "team_1", "agent_worker", "session_1"),
    ]

    with pytest.raises(TeamToolAuthorizationError):
        result = tool.handler(_invocation("team_other"))
        await cast(Awaitable[str], result)


@pytest.mark.parametrize("member", (None, _member(session_id="session_other")))
@async_test
async def test_team_claim_hydration_fails_closed_without_matching_active_binding(
    member: TeamMemberRecord | None,
) -> None:
    factory = _factory(_Teams(member), _WorkspaceAuthority())

    with pytest.raises(TeamMembershipError):
        await factory.hydrate(claimed=_claimed_with(_accepted_internal()))


@async_test
async def test_team_claim_hydration_rejects_invalid_workspace_authority_value() -> None:
    factory = _factory(_Teams(_member()), _InvalidWorkspaceAuthority())

    with pytest.raises(TypeError, match="WorkspaceHandle or None"):
        await factory.hydrate(claimed=_claimed_with(_accepted_internal()))


@async_test
async def test_generic_wakeup_does_not_gain_team_tools_from_payload_shape() -> None:
    teams = _Teams(_member())
    factory = _factory(teams, _WorkspaceAuthority())
    accepted = AcceptedContinuationInput(
        "run_1",
        "ordinary_command",
        "wakeup",
        TEAM_PAYLOAD,
        "turn_generic",
    )

    agent = await factory.hydrate(claimed=_claimed_with(accepted))

    assert teams.team_calls == []
    assert teams.member_calls == []
    with pytest.raises(KeyError):
        agent.query_loop.tool_call_router.tool_registry.get("team_read_messages")


def _invocation(team_id: str) -> ToolInvocation:
    return ToolInvocation(
        "team_read_messages",
        {"team_id": team_id},
        ToolInvocationContext(
            invocation_id="invocation_" + "4" * 32,
            operation_id="operation_" + "5" * 32,
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_team",
            tool_call_id="call_read",
            attempt=1,
        ),
    )
