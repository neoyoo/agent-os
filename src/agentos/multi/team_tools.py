from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolInvocation,
    ToolRegistry,
)
from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import RequestScope
from agentos.multi.team_errors import TeamBoundaryError, TeamToolAuthorizationError
from agentos.multi.team_ports import TeamWorkspaceAuthorityPort
from agentos.multi.team_runtime import TeamRuntime
from agentos.multi.team_tool_projection import (
    agent_create_schema,
    render_member,
    render_message_page,
    render_receipt,
    render_team,
    team_create_schema,
    team_delete_schema,
    team_read_messages_schema,
    team_say_schema,
)
from agentos.multi.team_types import (
    MAX_TEAM_MEMBER_CAPABILITIES,
    TEAM_MESSAGE_PAGE_LIMIT,
    TeamAccessContext,
    TeamAddressingKind,
)
from agentos.workspace import WorkspaceHandle


_MANAGEMENT_TOOLS = frozenset({"team_create", "agent_create", "team_delete"})
_TEAM_TOOLS = frozenset(
    {"team_create", "agent_create", "team_say", "team_read_messages", "team_delete"},
)


@dataclass(frozen=True, slots=True)
class TeamToolAuthorizationRequest:
    """不含正文的 Team Tool 资源级授权 intent。"""

    tool_name: str
    scope: RequestScope
    owner_agent_id: str
    owner_session_id: str
    team_id: str
    recipient_agent_id: str | None = None
    target_session_id: str | None = None
    capabilities: tuple[str, ...] = ()
    addressing_kind: TeamAddressingKind | None = None
    addressed_agent_id: str | None = None

    def __post_init__(self) -> None:
        if self.tool_name not in _TEAM_TOOLS:
            raise ValueError("tool_name is invalid")
        if type(self.scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        require_identifier(self.owner_agent_id, "owner_agent_id")
        require_identifier(self.owner_session_id, "owner_session_id")
        require_identifier(self.team_id, "team_id")
        if self.recipient_agent_id is not None:
            require_identifier(self.recipient_agent_id, "recipient_agent_id")
        if self.target_session_id is not None:
            require_identifier(self.target_session_id, "target_session_id")
        capabilities = _string_tuple(self.capabilities, "capabilities")
        if len(capabilities) > MAX_TEAM_MEMBER_CAPABILITIES:
            raise ValueError("capabilities cannot contain more than 32 values")
        if self.addressing_kind not in {None, "direct", "broadcast"}:
            raise ValueError("addressing_kind is invalid")
        if self.addressed_agent_id is not None:
            require_identifier(self.addressed_agent_id, "addressed_agent_id")
        if self.addressing_kind == "direct" and self.addressed_agent_id is None:
            raise ValueError("direct addressing requires addressed_agent_id")
        if self.addressing_kind != "direct" and self.addressed_agent_id is not None:
            raise ValueError("addressed_agent_id requires direct addressing")
        _validate_authorization_resource(self, capabilities)
        object.__setattr__(self, "capabilities", capabilities)


class TeamToolAuthorizationPolicy(Protocol):
    """LLM 可调用 Team 操作的受信任部署授权策略。"""

    def authorize_team_tool(self, request: TeamToolAuthorizationRequest) -> None:
        """校验当前 owner 是否可以执行指定 Team Tool。"""

        ...


class DefaultTeamToolAuthorizationPolicy:
    """在部署代码显式授权前拒绝 Team 管理操作。"""

    def authorize_team_tool(self, request: TeamToolAuthorizationRequest) -> None:
        """拒绝创建、添加成员和删除等管理操作。"""

        if request.tool_name in _MANAGEMENT_TOOLS:
            raise TeamToolAuthorizationError


class AllowAllTeamToolAuthorizationPolicy:
    """允许全部 Team 操作的显式本地或开发策略。"""

    def authorize_team_tool(self, request: TeamToolAuthorizationRequest) -> None:
        """允许当前 Team Tool 调用。"""

        return None


class TeamTools:
    """把 TeamRuntime Tool 绑定到受信任的租户、Agent 和 Session。"""

    def __init__(
        self,
        *,
        runtime: TeamRuntime,
        scope: RequestScope,
        owner_agent_id: str,
        owner_session_id: str,
        owner_workspace: WorkspaceHandle | None = None,
        owner_capabilities: tuple[str, ...] = (),
        authorization_policy: TeamToolAuthorizationPolicy | None = None,
        target_workspace_resolver: TeamWorkspaceAuthorityPort | None = None,
    ) -> None:
        if type(scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        require_identifier(owner_agent_id, "owner_agent_id")
        require_identifier(owner_session_id, "owner_session_id")
        if owner_workspace is not None and type(owner_workspace) is not WorkspaceHandle:
            raise TypeError("owner_workspace must be WorkspaceHandle or None")
        self.runtime = runtime
        self.scope = scope
        self.owner_agent_id = owner_agent_id
        self.owner_session_id = owner_session_id
        self.owner_workspace = owner_workspace
        self.owner_capabilities = _string_tuple(owner_capabilities, "owner_capabilities")
        self.authorization_policy = (
            authorization_policy or DefaultTeamToolAuthorizationPolicy()
        )
        self.target_workspace_resolver = target_workspace_resolver

    def register(self, registry: ToolRegistry) -> None:
        """注册五个规范的 async Team Tool。"""

        registry.register(
            RegisteredTool(
                name="team_create",
                description="Create a Team with this agent as its leader.",
                parameters=team_create_schema(),
                handler=self._team_create,
                side_effect_policy=SideEffectPolicy.NON_RETRYABLE,
            ),
        )
        registry.register(
            RegisteredTool(
                name="agent_create",
                description="Add a worker binding to a Team led by this agent.",
                parameters=agent_create_schema(),
                handler=self._agent_create,
                side_effect_policy=SideEffectPolicy.NON_RETRYABLE,
            ),
        )
        registry.register(
            RegisteredTool(
                name="team_say",
                description="Send an idempotent message as this Team member.",
                parameters=team_say_schema(),
                handler=self._team_say,
                side_effect_policy=SideEffectPolicy.NON_RETRYABLE,
            ),
        )
        registry.register(
            RegisteredTool(
                name="team_read_messages",
                description="Read Team messages visible to this member.",
                parameters=team_read_messages_schema(),
                handler=self._team_read_messages,
                side_effect_policy=SideEffectPolicy.PURE,
            ),
        )
        registry.register(
            RegisteredTool(
                name="team_delete",
                description="Delete a Team led by this agent.",
                parameters=team_delete_schema(),
                handler=self._team_delete,
                side_effect_policy=SideEffectPolicy.NON_RETRYABLE,
            ),
        )

    async def _team_create(self, invocation: ToolInvocation) -> str:
        arguments = invocation.arguments
        team_id = cast(str, arguments["team_id"])
        self._authorize(invocation, self._authorization_request("team_create", team_id))
        team = await self.runtime.create_team(
            scope=self.scope,
            team_id=team_id,
            leader_agent_id=self.owner_agent_id,
            leader_target_session_id=self.owner_session_id,
            workspace=self.owner_workspace,
            leader_capabilities=self.owner_capabilities,
        )
        return render_team(team)

    async def _agent_create(self, invocation: ToolInvocation) -> str:
        arguments = invocation.arguments
        team_id = cast(str, arguments["team_id"])
        recipient_agent_id = cast(str, arguments["recipient_agent_id"])
        target_session_id = cast(str, arguments["target_session_id"])
        capabilities = _string_tuple(arguments.get("capabilities", ()), "capabilities")
        request = self._authorization_request(
            "agent_create",
            team_id,
            recipient_agent_id=recipient_agent_id,
            target_session_id=target_session_id,
            capabilities=capabilities,
        )
        self._authorize(invocation, request)
        if self.target_workspace_resolver is None:
            raise TeamBoundaryError
        target_workspace = await self.target_workspace_resolver.resolve_target_workspace(
            scope=self.scope,
            target_session_id=target_session_id,
        )
        if target_workspace is not None and type(target_workspace) is not WorkspaceHandle:
            raise TypeError("workspace resolver must return WorkspaceHandle or None")
        member = await self.runtime.add_worker(
            scope=self.scope,
            team_id=team_id,
            access=self._access_context(team_id),
            recipient_agent_id=recipient_agent_id,
            target_session_id=target_session_id,
            capabilities=capabilities,
            target_workspace=target_workspace,
        )
        return render_member(member)

    async def _team_say(self, invocation: ToolInvocation) -> str:
        arguments = invocation.arguments
        team_id = cast(str, arguments["team_id"])
        addressing_kind = cast(TeamAddressingKind, arguments["addressing_kind"])
        addressed_agent_id = cast(str | None, arguments.get("addressed_agent_id"))
        self._authorize(
            invocation,
            self._authorization_request(
                "team_say",
                team_id,
                addressing_kind=addressing_kind,
                addressed_agent_id=addressed_agent_id,
            ),
        )
        receipt = await self.runtime.say(
            scope=self.scope,
            team_id=team_id,
            access=self._access_context(team_id),
            operation_id=invocation.context.operation_id,
            content=cast(str, arguments["content"]),
            addressing_kind=addressing_kind,
            addressed_agent_id=addressed_agent_id,
            message_kind=cast(str, arguments.get("message_kind", "observation")),  # type: ignore[arg-type]
            correlation_id=cast(str | None, arguments.get("correlation_id")),
        )
        return render_receipt(receipt)

    async def _team_read_messages(self, invocation: ToolInvocation) -> str:
        arguments = invocation.arguments
        team_id = cast(str, arguments["team_id"])
        self._authorize(
            invocation,
            self._authorization_request("team_read_messages", team_id),
        )
        page = await self.runtime.read_messages(
            scope=self.scope,
            team_id=team_id,
            access=self._access_context(team_id),
            after_message_id=cast(str | None, arguments.get("after_message_id")),
            limit=cast(int, arguments.get("limit", TEAM_MESSAGE_PAGE_LIMIT)),
        )
        return render_message_page(page)

    async def _team_delete(self, invocation: ToolInvocation) -> str:
        arguments = invocation.arguments
        team_id = cast(str, arguments["team_id"])
        self._authorize(invocation, self._authorization_request("team_delete", team_id))
        team = await self.runtime.delete_team(
            scope=self.scope,
            team_id=team_id,
            access=self._access_context(team_id),
        )
        return render_team(team)

    def _authorize(
        self,
        invocation: ToolInvocation,
        request: TeamToolAuthorizationRequest,
    ) -> None:
        context = invocation.context
        if (
            invocation.tool_name != request.tool_name
            or context.tenant_id != self.scope.tenant_id
            or context.session_id != self.owner_session_id
        ):
            raise TeamToolAuthorizationError
        self.authorization_policy.authorize_team_tool(request)

    def _authorization_request(
        self,
        tool_name: str,
        team_id: str,
        *,
        recipient_agent_id: str | None = None,
        target_session_id: str | None = None,
        capabilities: tuple[str, ...] = (),
        addressing_kind: TeamAddressingKind | None = None,
        addressed_agent_id: str | None = None,
    ) -> TeamToolAuthorizationRequest:
        return TeamToolAuthorizationRequest(
            tool_name=tool_name,
            scope=self.scope,
            owner_agent_id=self.owner_agent_id,
            owner_session_id=self.owner_session_id,
            team_id=team_id,
            recipient_agent_id=recipient_agent_id,
            target_session_id=target_session_id,
            capabilities=capabilities,
            addressing_kind=addressing_kind,
            addressed_agent_id=addressed_agent_id,
        )

    def _access_context(self, team_id: str) -> TeamAccessContext:
        return TeamAccessContext(
            tenant_id=self.scope.tenant_id,
            team_id=team_id,
            recipient_agent_id=self.owner_agent_id,
            target_session_id=self.owner_session_id,
        )


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if type(value) not in {tuple, list}:
        raise TypeError(f"{field_name} must contain identifier values")
    if len(value) > MAX_TEAM_MEMBER_CAPABILITIES:
        raise ValueError(f"{field_name} cannot contain more than 32 values")
    items = tuple(value)
    for item in items:
        require_identifier(item, field_name)
    if len(set(items)) != len(items):
        raise ValueError(f"{field_name} cannot contain duplicate values")
    return tuple(sorted(items))


def _validate_authorization_resource(
    request: TeamToolAuthorizationRequest,
    capabilities: tuple[str, ...],
) -> None:
    if request.tool_name == "agent_create":
        valid = (
            request.recipient_agent_id is not None
            and request.target_session_id is not None
            and request.addressing_kind is None
        )
    elif request.tool_name == "team_say":
        valid = (
            request.recipient_agent_id is None
            and request.target_session_id is None
            and not capabilities
            and request.addressing_kind is not None
        )
    else:
        valid = (
            request.recipient_agent_id is None
            and request.target_session_id is None
            and not capabilities
            and request.addressing_kind is None
        )
    if not valid:
        raise ValueError("team tool resource intent is invalid")


__all__ = [
    "AllowAllTeamToolAuthorizationPolicy",
    "DefaultTeamToolAuthorizationPolicy",
    "TeamToolAuthorizationPolicy",
    "TeamToolAuthorizationRequest",
    "TeamTools",
]
