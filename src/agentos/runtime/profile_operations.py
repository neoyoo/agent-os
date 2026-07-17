from __future__ import annotations

from dataclasses import dataclass


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
    """分布式 Web Session 的部署 readiness 契约。"""

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
        """返回尚未配置的必要部署组件。"""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """返回可序列化的分布式 Web Session 部署指引。"""

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
        """返回 ASGI readiness 兼容的检查结果。"""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    @staticmethod
    def _validate_component_names(
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


@dataclass(frozen=True, slots=True)
class WorkerProcessLifecycleDeploymentProfile:
    """Worker 进程生命周期的部署 readiness 契约。"""

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
        """返回尚未配置的必要 Worker 生命周期组件。"""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """返回可序列化的 Worker 生命周期部署指引。"""

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
        """返回 ASGI readiness 兼容的检查结果。"""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    @staticmethod
    def _validate_component_names(
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")
