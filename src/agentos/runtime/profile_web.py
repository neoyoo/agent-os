from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from agentos.runtime.agent import Agent
from agentos.workspace.models import WorkspaceHandle, WorkspaceProvider, WorkspaceRequest


class AgentSessionProviderLike(Protocol):
    """Web Profile 需要的 Session Provider 结构。"""

    def get_agent(self, session_id: str) -> Agent:
        """按 Session ID 返回 Agent。"""

    def release_agent(self, session_id: str, agent: Agent) -> None:
        """标记本轮 Channel 调用结束。"""


@dataclass(slots=True)
class WebRuntimeProfile:
    """依赖外部 Session Provider 的 Web Channel Profile。"""

    session_provider: AgentSessionProviderLike
    auth_policy: object | None = None
    rate_limiter: object | None = None
    readiness_checks: Mapping[str, Callable[[], object]] | None = None
    health_checks: Mapping[str, Callable[[], object]] | None = None
    a2a_server: object | None = None
    session_lifecycle: Literal["single-process", "durable-session"] = (
        "single-process"
    )
    workspace_provider: WorkspaceProvider | None = None
    workspace_request: WorkspaceRequest | None = None
    name: str = "web"
    workspace_handle: WorkspaceHandle | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """解析可选的 Web Workspace 元数据。"""

        if self.workspace_provider is None:
            return
        request = self.workspace_request or WorkspaceRequest(
            requested_scope="session",
        )
        self.workspace_handle = self.workspace_provider.resolve_workspace(request)

    def build_agent(self, session_id: str | None = None) -> Agent:
        """从 Session Provider 取得 Agent。"""

        if session_id is None:
            raise ValueError("WebRuntimeProfile requires a session_id")
        return self.session_provider.get_agent(session_id)

    def build_channel_app(self) -> object:
        """构建 ASGI Channel App。"""

        from agentos.channels import AsgiAgentApp

        return AsgiAgentApp(
            sessions=self.session_provider,
            auth_policy=self.auth_policy,
            rate_limiter=self.rate_limiter,
            readiness_checks=self.readiness_checks,
            health_checks=self.health_checks,
            a2a_server=self.a2a_server,
        )

    def readiness_metadata(self) -> dict[str, object]:
        """返回 Profile 层生产就绪元数据。"""

        return {
            "session_lifecycle": self.session_lifecycle,
            "workspace": (
                None
                if self.workspace_handle is None
                else {
                    "workspace_id": self.workspace_handle.workspace_id,
                    "scope": self.workspace_handle.scope,
                }
            ),
        }


@dataclass(slots=True)
class DistributedWebRuntimeProfile(WebRuntimeProfile):
    """组合 Durable Session Adapter 的分布式 Web Profile。"""

    agent_factory: object = field(default=None)
    lease_store: object = field(default=None)
    snapshot_persistence: object = field(default=None)
    sse_event_buffer: object | None = field(default=None)
    sse_turn_control: object | None = field(default=None)
    sse_turn_control_owner_id: str | None = None
    sse_turn_control_ttl_seconds: float = 60.0
    sse_turn_control_poll_interval_seconds: float | None = None
    session_lease_heartbeat_interval_seconds: float | None = None
    owner_id: str = "agentos-web-node"
    lease_ttl_seconds: float = 60.0
    acquire_timeout_seconds: float | None = 10.0
    name: str = "distributed-web"
    session_lifecycle: Literal["durable-session"] = "durable-session"

    def __init__(
        self,
        *,
        agent_factory: object,
        lease_store: object,
        snapshot_persistence: object,
        owner_id: str = "agentos-web-node",
        lease_ttl_seconds: float = 60.0,
        acquire_timeout_seconds: float | None = 10.0,
        auth_policy: object | None = None,
        rate_limiter: object | None = None,
        readiness_checks: Mapping[str, Callable[[], object]] | None = None,
        health_checks: Mapping[str, Callable[[], object]] | None = None,
        a2a_server: object | None = None,
        sse_event_buffer: object | None = None,
        sse_turn_control: object | None = None,
        sse_turn_control_owner_id: str | None = None,
        sse_turn_control_ttl_seconds: float = 60.0,
        sse_turn_control_poll_interval_seconds: float | None = None,
        session_lease_heartbeat_interval_seconds: float | None = None,
        workspace_provider: WorkspaceProvider | None = None,
        workspace_request: WorkspaceRequest | None = None,
        name: str = "distributed-web",
    ) -> None:
        """从分布式 Adapter 组装 Durable Web Session Profile。"""

        from agentos.channels import DurableAgentSessionProvider

        session_provider = DurableAgentSessionProvider(
            agent_factory=agent_factory,
            persistence=snapshot_persistence,
            lease_store=lease_store,
            owner_id=owner_id,
            lease_ttl_seconds=lease_ttl_seconds,
            acquire_timeout_seconds=acquire_timeout_seconds,
        )
        WebRuntimeProfile.__init__(
            self,
            session_provider=session_provider,
            auth_policy=auth_policy,
            rate_limiter=rate_limiter,
            readiness_checks={
                **dict(readiness_checks or {}),
                "distributed_stream_resume": self._stream_resume_readiness_check,
            },
            health_checks=health_checks,
            a2a_server=a2a_server,
            session_lifecycle="durable-session",
            workspace_provider=workspace_provider,
            workspace_request=workspace_request,
            name=name,
        )
        self.agent_factory = agent_factory
        self.lease_store = lease_store
        self.snapshot_persistence = snapshot_persistence
        self.sse_event_buffer = sse_event_buffer
        self.sse_turn_control = sse_turn_control
        self.sse_turn_control_owner_id = sse_turn_control_owner_id
        self.sse_turn_control_ttl_seconds = sse_turn_control_ttl_seconds
        self.sse_turn_control_poll_interval_seconds = (
            sse_turn_control_poll_interval_seconds
        )
        self.session_lease_heartbeat_interval_seconds = (
            session_lease_heartbeat_interval_seconds
        )
        self.owner_id = owner_id
        self.lease_ttl_seconds = lease_ttl_seconds
        self.acquire_timeout_seconds = acquire_timeout_seconds

    def build_channel_app(self) -> object:
        """构建带分布式 Stream Resume 设置的 ASGI App。"""

        from agentos.channels import AsgiAgentApp

        return AsgiAgentApp(
            sessions=self.session_provider,
            auth_policy=self.auth_policy,
            rate_limiter=self.rate_limiter,
            readiness_checks=self.readiness_checks,
            health_checks=self.health_checks,
            a2a_server=self.a2a_server,
            sse_event_buffer=self.sse_event_buffer,
            sse_turn_control=self.sse_turn_control,
            sse_turn_control_owner_id=self.sse_turn_control_owner_id,
            sse_turn_control_ttl_seconds=self.sse_turn_control_ttl_seconds,
            sse_turn_control_poll_interval_seconds=(
                self.sse_turn_control_poll_interval_seconds
            ),
            session_lease_heartbeat_interval_seconds=(
                self.session_lease_heartbeat_interval_seconds
            ),
        )

    def release_agent(self, session_id: str, agent: Agent) -> None:
        """释放并持久化通过此 Profile 取得的 Agent。"""

        self.session_provider.release_agent(session_id, agent)

    def readiness_metadata(self) -> dict[str, object]:
        """返回该 Profile 的生产就绪元数据。"""

        metadata = WebRuntimeProfile.readiness_metadata(self)
        stream_resume_gaps = self._stream_resume_gaps()
        stream_resume_evidence_gaps = self._stream_resume_evidence_gaps()
        stream_resume_configured = not stream_resume_gaps
        shared_backend_evidenced = (
            "shared_stream_backend_evidence" not in stream_resume_evidence_gaps
        )
        cross_node_evidenced = (
            "cross_node_resume_evidence" not in stream_resume_evidence_gaps
        )
        metadata.update(
            {
                "session_provider": self.session_provider.__class__.__name__,
                "lease_store": self.lease_store.__class__.__name__,
                "snapshot_persistence": (
                    self.snapshot_persistence.__class__.__name__
                ),
                "operations_profile": "DistributedWebSessionOperationsProfile",
                "sse_event_buffer": _adapter_name(self.sse_event_buffer),
                "sse_turn_control": _adapter_name(self.sse_turn_control),
                "sse_turn_control_owner_id": self.sse_turn_control_owner_id,
                "sse_turn_control_ttl_seconds": (
                    self.sse_turn_control_ttl_seconds
                ),
                "sse_turn_control_poll_interval_seconds": (
                    self.sse_turn_control_poll_interval_seconds
                ),
                "session_lease_heartbeat_interval_seconds": (
                    self.session_lease_heartbeat_interval_seconds
                ),
                "distributed_stream_resume_configured": stream_resume_configured,
                "distributed_stream_resume_shared_backend_evidenced": (
                    shared_backend_evidenced
                ),
                "distributed_stream_resume_cross_node_evidenced": (
                    cross_node_evidenced
                ),
                "distributed_stream_resume_ready": (
                    stream_resume_configured
                    and shared_backend_evidenced
                    and cross_node_evidenced
                ),
                "stream_resume_gaps": list(stream_resume_gaps),
                "stream_resume_evidence_gaps": list(
                    stream_resume_evidence_gaps,
                ),
                "owner_id": self.owner_id,
                "lease_ttl_seconds": self.lease_ttl_seconds,
                "acquire_timeout_seconds": self.acquire_timeout_seconds,
                "production_gaps": [
                    "auth and tenant policy",
                    "workspace enforcement",
                    "migration rollout",
                    "TTL and stale lease recovery policy",
                    "live backend verification",
                ],
            },
        )
        return metadata

    def _stream_resume_readiness_check(self) -> dict[str, object]:
        gaps = self._stream_resume_gaps()
        evidence_gaps = self._stream_resume_evidence_gaps()
        configured = [
            component
            for component in (
                "shared_sse_event_buffer",
                "sse_turn_control",
                "session_lease_heartbeat",
            )
            if component not in gaps
        ]
        ok = not gaps and not evidence_gaps
        return {
            "profile": self.__class__.__name__,
            "check_name": "distributed_stream_resume",
            "status": "ok" if ok else "failed",
            "ok": ok,
            "configured_components": configured,
            "missing_components": list(gaps),
            "missing_evidence": list(evidence_gaps),
            "sdk_owned": (
                "SseEventBuffer wiring",
                "SseTurnControlStore wiring",
                "session lease heartbeat wiring",
            ),
            "deployment_owned": (
                "shared stream backend deployment",
                "turn-control backend deployment",
                "heartbeat interval policy",
            ),
        }

    def _stream_resume_gaps(self) -> tuple[str, ...]:
        gaps: list[str] = []
        if self.sse_event_buffer is None:
            gaps.append("shared_sse_event_buffer")
        if self.sse_turn_control is None:
            gaps.append("sse_turn_control")
        if self.session_lease_heartbeat_interval_seconds is None:
            gaps.append("session_lease_heartbeat")
        return tuple(gaps)

    def _stream_resume_evidence_gaps(self) -> tuple[str, ...]:
        gaps: list[str] = []
        shared_backend_evidenced = bool(
            getattr(self.sse_event_buffer, "agentos_shared_backend_evidenced", False),
        ) and bool(
            getattr(self.sse_turn_control, "agentos_shared_backend_evidenced", False),
        )
        cross_node_evidenced = bool(
            getattr(self.sse_event_buffer, "agentos_cross_node_resume_evidenced", False),
        )
        if not shared_backend_evidenced:
            gaps.append("shared_stream_backend_evidence")
        if not cross_node_evidenced:
            gaps.append("cross_node_resume_evidence")
        return tuple(gaps)


def _adapter_name(adapter: object | None) -> str | None:
    if adapter is None:
        return None
    return adapter.__class__.__name__
