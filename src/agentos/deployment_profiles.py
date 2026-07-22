from __future__ import annotations

from dataclasses import dataclass

from agentos.deployment_constants import (
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
)
from agentos.deployment_reports import DeploymentLiveBackendVerificationGateReport
from agentos.deployment_types import BackendVerificationRecord


@dataclass(frozen=True, slots=True)
class DeploymentLiveBackendVerificationProfile:
    """Readiness profile for deployment-owned live backend verification evidence."""

    records: tuple[BackendVerificationRecord, ...] = ()
    required_backends: tuple[str, ...] = LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    probe_name: str = "deployment_live_backend_verification"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_backends:
            raise ValueError("required_backends must not be empty")
        if any(not backend.strip() for backend in self.required_backends):
            raise ValueError("required_backends must not contain empty names")

    def gate_report(self) -> DeploymentLiveBackendVerificationGateReport:
        """Return the readiness gate for the configured verification records."""

        return DeploymentLiveBackendVerificationGateReport.from_records(
            self.records,
            required_backends=self.required_backends,
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return readiness-compatible live verification metadata."""

        report = self.gate_report()
        payload = report.as_dict()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": report.accepted,
            **payload,
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


@dataclass(frozen=True, slots=True)
class ProductionStatePlaneDeploymentProfile:
    """生产状态平面就绪契约，不负责执行具体后端。"""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS
    )
    probe_name: str = "production_state_plane"

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
        """返回尚未配置的生产状态平面组件。"""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """返回 JSON-safe 的生产状态平面部署指导。"""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "state_planes": {
                "agent_registry": {
                    "responsibility": (
                        "Registry discovers agents and workers by AgentCard, "
                        "endpoint, capabilities, version, and health metadata"
                    ),
                    "recommended_boundary": (
                        "NacosAgentRegistryAdapter or custom registry adapter"
                    ),
                    "not_responsible_for": (
                        "task truth",
                        "plan truth",
                        "session runtime snapshots",
                        "worker runtime state",
                    ),
                },
                "message_queue": {
                    "responsibility": (
                        "Queue delivers messages and wakeups through inbox, "
                        "delivery, and fan-out hints"
                    ),
                    "recommended_boundary": (
                        "RedisAgentMessageQueue or custom queue adapter"
                    ),
                    "not_responsible_for": (
                        "final task status",
                        "final plan status",
                        "session snapshots",
                    ),
                },
                "task_store": {
                    "responsibility": (
                        "Truth state lives in stores; task store owns task "
                        "truth, execution result, retry state, and assignment "
                        "evidence"
                    ),
                    "recommended_boundary": (
                        "PostgresTaskStore or custom task store"
                    ),
                    "not_responsible_for": (
                        "agent discovery",
                        "message delivery",
                        "worker process lifecycle",
                    ),
                },
                "plan_store": {
                    "responsibility": (
                        "Plan store owns plan truth, step state, claims, "
                        "scheduler recovery metadata, and planner execution "
                        "evidence"
                    ),
                    "recommended_boundary": (
                        "PostgresPlanStore plus PostgresPlanClaimStore or "
                        "custom plan stores"
                    ),
                    "not_responsible_for": (
                        "agent discovery",
                        "message delivery",
                        "process supervision",
                    ),
                },
                "worker_process_supervisor": {
                    "responsibility": (
                        "Worker supervisor owns local process start, running, "
                        "stop, exit, failure, exit code, and timestamp evidence"
                    ),
                    "recommended_boundary": (
                        "WorkerProcessSupervisor or deployment-owned job runner"
                    ),
                    "not_responsible_for": (
                        "task truth",
                        "plan truth",
                        "session snapshots",
                        "autoscaling policy",
                    ),
                },
                "session_snapshot_persistence": {
                    "responsibility": (
                        "Session persistence owns context, messages, "
                        "compression, working state, and session runtime "
                        "snapshots"
                    ),
                    "recommended_boundary": (
                        "SessionSnapshotPersistence, "
                        "PostgresSessionSnapshotPersistence, or custom "
                        "SessionPersistence"
                    ),
                    "not_responsible_for": (
                        "agent discovery",
                        "worker process status",
                        "task truth",
                        "plan truth",
                    ),
                },
            },
            "sdk_owned": (
                "ProductionStatePlaneDeploymentProfile",
                "state-plane responsibility metadata",
                "readiness-compatible profile payloads",
                "AgentCard and registry protocols",
                "AgentMessageQueue protocol",
                "TaskStore protocol",
                "PlanStore and PlanClaimStore protocols",
                "SessionPersistence protocol",
            ),
            "deployment_owned": (
                "Nacos registry deployment and credentials",
                "Redis queue deployment and credentials",
                "Postgres task, plan, claim, and snapshot migrations",
                "worker process supervisor implementation",
                "secret distribution",
                "tenant directory integration",
                "autoscaling policy",
                "alert routing and runbooks",
                "live backend verification",
            ),
            "boundary_policy": (
                "registry is not task truth",
                "queue is not final task or plan state",
                "task and plan stores are not process supervisors",
                "worker lifecycle evidence is not session runtime state",
                "session snapshots are not registry discovery metadata",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """返回可接入 readiness endpoint 的检查载荷。"""

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


__all__ = [
    "DeploymentLiveBackendVerificationProfile",
    "ProductionStatePlaneDeploymentProfile",
]
