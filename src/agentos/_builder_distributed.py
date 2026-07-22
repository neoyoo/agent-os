from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from agentos._builder_hydration import hydrate_runtime_checkpoint
from agentos._builder_state import RuntimeStateComponents
from agentos.artifacts import ArtifactRuntime
from agentos.capabilities import RegisteredTool
from agentos.context import ContextRuntime
from agentos.distributed._claim_artifacts import ClaimArtifactStore
from agentos.distributed._tool_result_artifacts import (
    DistributedToolResultRefProjector,
)
from agentos.distributed.models import ClaimedExecution, RequestScope
from agentos.distributed.protocols import DistributedArtifactPort
from agentos.messages import MessageRuntime
from agentos.multi.team_errors import (
    TeamBoundaryError,
    TeamMembershipError,
    TeamNotFoundError,
)
from agentos.multi.team_identity import team_delivery_id as canonical_team_delivery_id
from agentos.multi.team_ports import (
    TeamApplicationPort,
    TeamWorkspaceAuthorityPort,
)
from agentos.multi.team_runtime import TeamRuntime
from agentos.multi.team_tools import TeamToolAuthorizationPolicy, TeamTools
from agentos.multi.team_types import (
    TeamAccessContext,
    TeamMemberRecord,
    TeamRecord,
)
from agentos.policies import ToolResultBudget
from agentos.runtime.agent import Agent
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.durable_runtime import DurableStateStore
from agentos.runtime.execution import AcceptedInternalStartInput, AcceptedTurnInput
from agentos.runtime.internal_start import normalize_internal_start_payload
from agentos.runtime.payloads import PayloadProtectionContext, PayloadProtector
from agentos.runtime.run_runtime import RunRuntime
from agentos.runtime.session import SessionState
from agentos.runtime.side_effect_resume_validator import SideEffectResumeValidator
from agentos.runtime.side_effect_store import SideEffectStore
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.tokens import TokenCounter
from agentos.workspace import WorkspaceHandle

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.builder import AgentBuilder


class ScopedStateStoreFactory(Protocol):
    def bind(self, scope: RequestScope) -> DurableStateStore: ...


@dataclass(frozen=True, slots=True)
class ClaimScopedAgentFactory:
    """Hydrate one isolated Agent from a PostgreSQL-authoritative claim."""

    builder: AgentBuilder
    state_store: ScopedStateStoreFactory
    artifact_store: DistributedArtifactPort
    side_effect_store: SideEffectStore
    side_effect_resume_validator: SideEffectResumeValidator
    payload_protector: PayloadProtector | None = None
    team_port: TeamApplicationPort | None = None
    team_workspace_authority: TeamWorkspaceAuthorityPort | None = None
    team_tool_authorization_policy: TeamToolAuthorizationPolicy | None = None

    def __post_init__(self) -> None:
        validate_distributed_builder(self.builder)
        if (self.team_port is None) != (self.team_workspace_authority is None):
            raise ValueError(
                "team port and workspace authority must be configured together",
            )

    async def hydrate(self, *, claimed: ClaimedExecution) -> Agent:
        if type(claimed) is not ClaimedExecution:
            raise TypeError("claimed must be ClaimedExecution")
        scope = claimed.target.scope
        session_id = claimed.target.session_id
        claim_tools = await self._claim_tools(claimed)
        store = self.state_store.bind(scope)
        checkpoint = await store.load_checkpoint(session_id)
        payloads = ToolPayloadRuntime(
            self.payload_protector,
            PayloadProtectionContext(scope.tenant_id, session_id),
        )
        if checkpoint is None:
            session = SessionState(session_id)
            messages = MessageRuntime()
            context = ContextRuntime(
                event_bus=self.builder._event_bus,
                session_id=session_id,
            )
        else:
            session, messages, context = hydrate_runtime_checkpoint(
                checkpoint,
                self.builder._event_bus,
                payloads,
            )
        state = RuntimeStateComponents(
            context=context,
            artifacts=ArtifactRuntime(
                session_id=session_id,
                store=ClaimArtifactStore(scope, session_id, self.artifact_store),
                event_bus=self.builder._event_bus,
            ),
            runs=RunRuntime(session_id=session_id, store=store),
            session=session,
        )
        checkpoint_source = RuntimeCheckpointSource(
            session,
            messages,
            context,
            payloads,
        )
        kwargs = self.builder._query_loop_kwargs(
            session_id,
            state=state,
            messages=messages,
            injected=claim_tools,
        )
        kwargs["checkpoint_source"] = checkpoint_source
        kwargs["checkpoint_store"] = store
        kwargs["side_effect_store"] = self.side_effect_store
        kwargs["side_effect_resume_validator"] = self.side_effect_resume_validator
        kwargs["tool_payload_runtime"] = payloads
        kwargs["tool_result_ref_projector"] = DistributedToolResultRefProjector(
            scope=scope,
            session_id=session_id,
            artifacts=self.artifact_store,
            budget=cast(ToolResultBudget, kwargs["tool_result_budget"]),
            token_counter=cast(TokenCounter, kwargs["token_counter"]),
        )
        return Agent(query_loop_kwargs=kwargs)

    async def _claim_tools(
        self,
        claimed: ClaimedExecution,
    ) -> tuple[RegisteredTool, ...]:
        payload = _team_source_payload(
            claimed.execution.input,
            claimed.target.scope,
            claimed.target.session_id,
        )
        if payload is None:
            return ()
        if self.team_port is None or self.team_workspace_authority is None:
            raise TeamBoundaryError

        scope = claimed.target.scope
        session_id = claimed.target.session_id
        team_id = payload["team_id"]
        recipient_agent_id = payload["recipient_agent_id"]
        team = await self.team_port.get_team(scope=scope, team_id=team_id)
        member = await self.team_port.get_member(
            scope=scope,
            team_id=team_id,
            recipient_agent_id=recipient_agent_id,
        )
        if team is not None and type(team) is not TeamRecord:
            raise TypeError("team port must return TeamRecord or None")
        if member is not None and type(member) is not TeamMemberRecord:
            raise TypeError("team port must return TeamMemberRecord or None")
        if team is None or team.status != "active" or team.team_id != team_id:
            raise TeamNotFoundError
        if (
            member is None
            or member.status != "active"
            or member.team_id != team_id
            or member.recipient_agent_id != recipient_agent_id
            or member.target_session_id != session_id
        ):
            raise TeamMembershipError

        owner_workspace = (
            await self.team_workspace_authority.resolve_target_workspace(
                scope=scope,
                target_session_id=session_id,
            )
        )
        if owner_workspace is not None and type(owner_workspace) is not WorkspaceHandle:
            raise TypeError("workspace authority must return WorkspaceHandle or None")
        runtime = TeamRuntime(port=self.team_port)
        runtime.validate_member_workspace(team.workspace, owner_workspace)
        access = TeamAccessContext(
            scope.tenant_id,
            team_id,
            member.recipient_agent_id,
            member.target_session_id,
        )
        return TeamTools.claim_scoped(
            runtime=runtime,
            scope=scope,
            access=access,
            owner_workspace=owner_workspace,
            owner_capabilities=member.capabilities,
            authorization_policy=self.team_tool_authorization_policy,
            target_workspace_resolver=self.team_workspace_authority,
        ).registered_tools()


def validate_distributed_builder(builder: AgentBuilder) -> None:
    forbidden = (
        builder._context_runtime,
        builder._message_runtime,
        builder._compression_runtime,
        builder._tool_call_router,
    )
    if any(item is not None for item in forbidden):
        raise ValueError("distributed profile rejects pre-bound runtime state")
    if builder._context_projection_providers is not None:
        raise ValueError(
            "distributed profile rejects static context projections",
        )


def _team_source_payload(
    input: AcceptedTurnInput,
    scope: RequestScope,
    session_id: str,
) -> dict[str, str] | None:
    if type(input) is AcceptedInternalStartInput:
        frozen = input.source_payload
    elif (
        type(input) is AcceptedContinuationInput
        and input.kind == "wakeup"
        and input.team_delivery_id is not None
    ):
        frozen = normalize_internal_start_payload(input.payload)
    else:
        return None
    payload = {
        "team_id": cast(str, frozen["team_id"]),
        "message_id": cast(str, frozen["message_id"]),
        "recipient_agent_id": cast(str, frozen["recipient_agent_id"]),
        "action": cast(str, frozen["action"]),
    }
    if type(input) is AcceptedContinuationInput and input.team_delivery_id != (
        canonical_team_delivery_id(
            scope=scope,
            team_id=payload["team_id"],
            message_id=payload["message_id"],
            recipient_agent_id=payload["recipient_agent_id"],
            target_session_id=session_id,
        )
    ):
        raise TeamBoundaryError
    return payload


__all__ = ["ClaimScopedAgentFactory", "validate_distributed_builder"]
