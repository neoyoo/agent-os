from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from agentos.deployment import (
    PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
    ProductionStatePlaneDeploymentProfile,
)
from agentos.runtime.agent import Agent
from agentos.workspace import WorkspaceHandle, WorkspaceProvider, WorkspaceRequest


class RuntimeProfile(Protocol):
    """部署形态装配边界，不负责执行 turn。"""

    name: str

    def build_agent(self, session_id: str | None = None) -> Agent:
        """构建当前 profile 下的 Agent。"""


class ChannelRuntimeProfile(RuntimeProfile, Protocol):
    """可暴露 channel app 的 runtime profile。"""

    def build_channel_app(self) -> object:
        """构建 ASGI 或其他 channel app。"""


class DistributedRuntimeProfile(ChannelRuntimeProfile, Protocol):
    """包含分布式 session、registry 或 task 边界的 runtime profile。"""

    def readiness_checks(self) -> dict[str, object]:
        """返回可接入 readiness endpoint 的检查项。"""


class RuntimeCompositionProfile(Protocol):
    """Profile that assembles runtime boundaries without building one Agent."""

    name: str

    def readiness_checks(self) -> dict[str, object]:
        """Return profile-level readiness checks."""


DISTRIBUTED_WEB_SESSION_OPERATIONS_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "durable_session_provider",
    "lease_store",
    "snapshot_persistence",
    "snapshot_migration",
    "lease_ttl_policy",
    "stale_lease_recovery",
    "shared_sse_event_buffer",
    "sse_turn_control",
    "session_lease_heartbeat",
    "credential_policy",
    "auth_tenant_policy",
    "workspace_policy",
    "live_backend_verification",
)


WORKER_PROCESS_LIFECYCLE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "process_supervisor",
    "restart_policy",
    "graceful_shutdown",
    "health_probe",
    "readiness_probe",
    "scaling_policy",
    "credential_policy",
    "migration_policy",
    "alerting",
    "live_backend_verification",
)


@dataclass(frozen=True, slots=True)
class DistributedWebSessionOperationsProfile:
    """Deployment-facing readiness contract for distributed web sessions."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        DISTRIBUTED_WEB_SESSION_OPERATIONS_REQUIRED_COMPONENTS
    )
    probe_name: str = "distributed_web_session_operations"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        self._validate_component_names(
            self.required_components,
            field_name="required_components",
        )
        self._validate_component_names(
            self.configured_components,
            field_name="configured_components",
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required operational components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for distributed web sessions."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "DistributedWebRuntimeProfile",
                "DurableAgentSessionProvider",
                "SnapshotAgentFactory",
                "SessionLeaseStore",
                "distributed lease adapter",
                "SessionSnapshot",
                "SessionPersistence",
                "durable snapshot persistence adapter",
                "SseEventBuffer",
                "SseTurnControlStore",
                "session lease heartbeat wiring",
                "acquire/hydrate/save/release lifecycle",
                "async session provider offload",
            ),
            "deployment_owned": (
                "external datastore credentials",
                "migration execution",
                "lease TTL tuning",
                "stale lease recovery policy",
                "shared SSE buffer deployment",
                "SSE turn control deployment",
                "lease heartbeat interval policy",
                "auth and tenant integration",
                "workspace policy configuration",
                "live backend verification",
                "rollout and rollback policy",
                "alerting and incident response",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")

@dataclass(frozen=True, slots=True)
class WorkerProcessLifecycleDeploymentProfile:
    """Deployment-facing readiness contract for worker process lifecycle."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        WORKER_PROCESS_LIFECYCLE_REQUIRED_COMPONENTS
    )
    probe_name: str = "worker_process_lifecycle"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        self._validate_component_names(
            self.required_components,
            field_name="required_components",
        )
        self._validate_component_names(
            self.configured_components,
            field_name="configured_components",
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required worker lifecycle components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for worker lifecycle."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "TeamWorkerRunner",
                "TeamWorkerDaemon",
                "TeamWorkerDaemonState",
                "PlannerRuntime.scheduler_tick",
                "PlanSchedulerTickReport",
                "DistributedTeamRuntimeProfile",
                "PlannerOrchestrationDeploymentProfile",
                "WorkspaceExecutionIsolationProfile",
                "readiness-compatible profile payloads",
            ),
            "deployment_owned": (
                "process supervisor or job runner",
                "restart policy",
                "graceful shutdown and draining",
                "horizontal scaling policy",
                "credentials and secret distribution",
                "schema migration execution",
                "live backend verification",
                "health/readiness endpoint wiring",
                "alert routing and runbooks",
                "OS/container sandboxing",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


class AgentBuilderLike(Protocol):
    """Local profile 需要的最小 AgentBuilder 结构。"""

    def build(self) -> Agent:
        """构建同步 Agent。"""

    def build_async(self) -> Agent:
        """构建异步 Agent。"""


class AgentSessionProviderLike(Protocol):
    """Web profile 需要的 session provider 结构。"""

    def get_agent(self, session_id: str) -> Agent:
        """按 session id 返回 Agent。"""

    def release_agent(self, session_id: str, agent: Agent) -> None:
        """标记本轮 channel 调用结束。"""


@dataclass(slots=True)
class LocalRuntimeProfile:
    """本地 terminal/script 形态 profile。"""

    agent_builder: AgentBuilderLike
    loop_mode: Literal["sync", "async"] = "sync"
    workspace_provider: WorkspaceProvider | None = None
    workspace_request: WorkspaceRequest | None = None
    name: str = "local"
    workspace_handle: WorkspaceHandle | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """Resolve optional local workspace metadata."""

        if self.workspace_provider is None:
            return
        request = self.workspace_request or WorkspaceRequest(
            requested_scope="process",
        )
        self.workspace_handle = self.workspace_provider.resolve_workspace(request)

    def build_agent(self, session_id: str | None = None) -> Agent:
        """按 sync/async 模式构建本地 Agent。"""

        if self.loop_mode == "async":
            return self.agent_builder.build_async()
        return self.agent_builder.build()


@dataclass(slots=True)
class WebRuntimeProfile:
    """Web channel 形态 profile，依赖外部 session provider。"""

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
        """Resolve optional web workspace metadata."""

        if self.workspace_provider is None:
            return
        request = self.workspace_request or WorkspaceRequest(
            requested_scope="session",
        )
        self.workspace_handle = self.workspace_provider.resolve_workspace(request)

    def build_agent(self, session_id: str | None = None) -> Agent:
        """从 session provider 取出 Agent。"""

        if session_id is None:
            raise ValueError("WebRuntimeProfile requires a session_id")
        return self.session_provider.get_agent(session_id)

    def build_channel_app(self) -> object:
        """构建 ASGI channel app。"""

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
        """返回 profile 层面的生产就绪元数据。"""

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
    """Production-oriented web profile with durable distributed sessions."""

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
        """Assemble a durable web session profile from distributed adapters."""

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
                "distributed_stream_resume": (
                    self._stream_resume_readiness_check
                ),
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
        """Build an ASGI app with distributed stream resume settings."""

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
        """Release and persist an agent acquired through this profile."""

        self.session_provider.release_agent(session_id, agent)

    def readiness_metadata(self) -> dict[str, object]:
        """Return production-readiness metadata for the preset."""

        metadata = WebRuntimeProfile.readiness_metadata(self)
        stream_resume_gaps = self._stream_resume_gaps()
        stream_resume_evidence_gaps = self._stream_resume_evidence_gaps()
        stream_resume_configured = not stream_resume_gaps
        stream_resume_shared_backend_evidenced = (
            "shared_stream_backend_evidence" not in stream_resume_evidence_gaps
        )
        stream_resume_cross_node_evidenced = (
            "cross_node_resume_evidence" not in stream_resume_evidence_gaps
        )
        metadata.update(
            {
                "session_provider": self.session_provider.__class__.__name__,
                "lease_store": self.lease_store.__class__.__name__,
                "snapshot_persistence": (
                    self.snapshot_persistence.__class__.__name__
                ),
                "operations_profile": (
                    "DistributedWebSessionOperationsProfile"
                ),
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
                "distributed_stream_resume_configured": (
                    stream_resume_configured
                ),
                "distributed_stream_resume_shared_backend_evidenced": (
                    stream_resume_shared_backend_evidenced
                ),
                "distributed_stream_resume_cross_node_evidenced": (
                    stream_resume_cross_node_evidenced
                ),
                "distributed_stream_resume_ready": (
                    stream_resume_configured
                    and stream_resume_shared_backend_evidenced
                    and stream_resume_cross_node_evidenced
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


@dataclass(slots=True)
class DistributedTeamRuntimeProfile:
    """Production-oriented profile for distributed team discussion agents."""

    store: object
    message_queue: object
    worker_session_provider: object
    worker_agent_provider: object | None = None
    retry_policy: object | None = None
    retry_store: object | None = None
    cancellation_store: object | None = None
    ui_stream: object | None = None
    notice_store: object | None = None
    wakeup_trigger: object | None = None
    daemon_team_id: str | None = None
    daemon_poll_interval_seconds: float = 0.5
    clock: object | None = None
    id_factory: object | None = None
    readiness: Mapping[str, object] = field(default_factory=dict)
    name: str = "distributed-team"
    team_runtime: object = field(init=False)
    worker_runner: object | None = field(init=False, default=None)
    worker_daemon: object | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """Assemble team runtime and optional worker host boundaries."""

        from agentos.multi import (
            TeamRuntime,
            TeamWorkerDaemon,
            TeamWorkerRunner,
        )

        self.team_runtime = TeamRuntime(
            store=self.store,
            message_queue=self.message_queue,
            notice_store=self.notice_store,
            wakeup_trigger=self.wakeup_trigger,
            worker_session_provider=self.worker_session_provider,
            ui_stream=self.ui_stream,
            clock=self.clock,
            id_factory=self.id_factory,
        )
        if self.worker_agent_provider is None:
            return
        self.worker_runner = TeamWorkerRunner(
            session_provider=self.worker_session_provider,
            agent_provider=self.worker_agent_provider,
            message_queue=self.message_queue,
            retry_policy=self.retry_policy,
            retry_store=self.retry_store,
            cancellation_store=self.cancellation_store,
            ui_stream=self.ui_stream,
            clock=self.clock,
        )
        self.worker_daemon = TeamWorkerDaemon(
            runner=self.worker_runner,
            team_id=self.daemon_team_id,
            poll_interval_seconds=self.daemon_poll_interval_seconds,
            clock=self.clock,
        )

    def build_team_runtime(self) -> object:
        """Return the assembled TeamRuntime."""

        return self.team_runtime

    def build_team_tools(self, *, owner_agent_id: str) -> object:
        """Return LLM-callable team tools for one owner agent."""

        from agentos.multi import TeamTools

        return TeamTools(
            runtime=self.team_runtime,
            owner_agent_id=owner_agent_id,
        )

    def build_worker_runner(self) -> object:
        """Return the assembled TeamWorkerRunner."""

        if self.worker_runner is None:
            raise NotImplementedError(
                "DistributedTeamRuntimeProfile requires worker_agent_provider "
                "to build worker runner",
            )
        return self.worker_runner

    def build_worker_daemon(self) -> object:
        """Return the assembled TeamWorkerDaemon."""

        if self.worker_daemon is None:
            raise NotImplementedError(
                "DistributedTeamRuntimeProfile requires worker_agent_provider "
                "to build worker daemon",
            )
        return self.worker_daemon

    def readiness_checks(self) -> dict[str, object]:
        """Return profile-level readiness checks."""

        checks = {
            "profile": self.name,
            "team_runtime": self.team_runtime.__class__.__name__,
            "team_store": self.store.__class__.__name__,
            "message_queue": self.message_queue.__class__.__name__,
            "worker_session_provider": (
                self.worker_session_provider.__class__.__name__
            ),
            "worker_runner": self._adapter_name(self.worker_runner),
            "worker_daemon": self._adapter_name(self.worker_daemon),
            "ui_stream": self._adapter_name(self.ui_stream),
            "retry_store": self._adapter_name(self.retry_store),
            "cancellation_store": self._adapter_name(self.cancellation_store),
        }
        checks.update(dict(self.readiness))
        return checks

    def readiness_metadata(self) -> dict[str, object]:
        """Return production-readiness metadata for the team preset."""

        return {
            "profile": self.name,
            "team_runtime": self.team_runtime.__class__.__name__,
            "team_store": self.store.__class__.__name__,
            "message_queue": self.message_queue.__class__.__name__,
            "worker_session_provider": (
                self.worker_session_provider.__class__.__name__
            ),
            "worker_agent_provider": self._adapter_name(
                self.worker_agent_provider,
            ),
            "worker_runner": self._adapter_name(self.worker_runner),
            "worker_daemon": self._adapter_name(self.worker_daemon),
            "retry_policy": self._adapter_name(self.retry_policy),
            "retry_store": self._adapter_name(self.retry_store),
            "cancellation_store": self._adapter_name(self.cancellation_store),
            "ui_stream": self._adapter_name(self.ui_stream),
            "daemon_team_id": self.daemon_team_id,
            "daemon_poll_interval_seconds": self.daemon_poll_interval_seconds,
            "production_gaps": [
                "credentials and migration rollout",
                "process supervision",
                "worker scaling policy",
                "OS/container sandboxing",
                "live backend verification",
            ],
        }

    def _adapter_name(self, adapter: object | None) -> str | None:
        return _adapter_name(adapter)


def _adapter_name(adapter: object | None) -> str | None:
    if adapter is None:
        return None
    return adapter.__class__.__name__


@dataclass(slots=True)
class DistributedAgentProfile:
    """分布式任务协调 profile，不代表自动 team worker session runtime。"""

    coordinator: object
    resolver: object | None = None
    registry: object | None = None
    remote_task_executor: object | None = None
    readiness: Mapping[str, object] = field(default_factory=dict)
    name: str = "distributed-agent"

    def readiness_checks(self) -> dict[str, object]:
        """返回配置的 readiness checks。"""

        return dict(self.readiness)
