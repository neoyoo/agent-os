from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from agentos.runtime.agent import Agent
from agentos.workspace.models import WorkspaceHandle, WorkspaceProvider, WorkspaceRequest


class AgentBuilderLike(Protocol):
    """Local Profile 需要的最小 AgentBuilder 结构。"""

    def build(self, *, session_id: str | None = None) -> Agent:
        """构建统一的 async-first Agent。"""


@dataclass(slots=True)
class LocalRuntimeProfile:
    """本地 terminal/script 形态 Profile。"""

    agent_builder: AgentBuilderLike
    workspace_provider: WorkspaceProvider | None = None
    workspace_request: WorkspaceRequest | None = None
    name: str = "local"
    workspace_handle: WorkspaceHandle | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """解析可选的本地 Workspace 元数据。"""

        if self.workspace_provider is None:
            return
        request = self.workspace_request or WorkspaceRequest(
            requested_scope="process",
        )
        self.workspace_handle = self.workspace_provider.resolve_workspace(request)

    def build_agent(self, session_id: str | None = None) -> Agent:
        """构建 Agent，并保持调用方提供的 Session ID。"""

        return self.agent_builder.build(session_id=session_id)
