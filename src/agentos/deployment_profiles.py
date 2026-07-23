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
                "postgres_state_store": {
                    "responsibility": (
                        "PostgreSQL owns Run, accepted input, claim/fence, "
                        "checkpoint, outbox, and side-effect truth"
                    ),
                    "recommended_boundary": "PostgresStateStore",
                    "not_responsible_for": (
                        "Redis delivery",
                        "live event replay",
                        "blob bytes",
                    ),
                },
                "postgres_artifact_store": {
                    "responsibility": (
                        "PostgreSQL owns Artifact metadata and idempotent upload "
                        "or deletion state while BlobStore owns shared bytes"
                    ),
                    "recommended_boundary": "PostgresArtifactStore",
                    "not_responsible_for": (
                        "Run state transitions",
                        "queue delivery",
                        "Provider context projection",
                    ),
                },
                "redis_worker_queue": {
                    "responsibility": (
                        "Redis delivers execution wakeups and supports pending "
                        "reclaim with explicit ACK"
                    ),
                    "recommended_boundary": "RedisQueueAdapter",
                    "not_responsible_for": (
                        "Run truth",
                        "claim/fence truth",
                        "terminal outcome",
                    ),
                },
                "redis_relay_queue": {
                    "responsibility": (
                        "Redis carries outbox deliveries using the stable "
                        "PostgreSQL outbox identity"
                    ),
                    "recommended_boundary": "RedisQueueAdapter",
                    "not_responsible_for": (
                        "outbox truth",
                        "submission idempotency",
                        "command state",
                    ),
                },
                "redis_event_replay": {
                    "responsibility": (
                        "Redis provides bounded typed live-event replay and tail"
                    ),
                    "recommended_boundary": "RedisEventReplayAdapter",
                    "not_responsible_for": (
                        "authoritative Run history",
                        "StoredMessage truth",
                        "Provider transcript persistence",
                    ),
                },
                "distributed_worker": {
                    "responsibility": (
                        "Worker claims execution, hydrates authoritative state, "
                        "runs Agent, commits through RunDriver, then ACKs"
                    ),
                    "recommended_boundary": "DistributedWorker",
                    "not_responsible_for": (
                        "database migrations",
                        "ingress authorization",
                        "autoscaling policy",
                    ),
                },
            },
            "sdk_owned": (
                "ProductionStatePlaneDeploymentProfile",
                "state-plane responsibility metadata",
                "readiness-compatible profile payloads",
                "PostgresStateStore",
                "PostgresArtifactStore",
                "RedisQueueAdapter",
                "RedisEventReplayAdapter",
                "DistributedWorker",
            ),
            "deployment_owned": (
                "PostgreSQL and Redis deployment and credentials",
                "distributed runtime migration execution",
                "shared BlobStore deployment",
                "worker process hosting and autoscaling",
                "secret distribution",
                "tenant directory integration",
                "autoscaling policy",
                "alert routing and runbooks",
                "live backend verification",
            ),
            "boundary_policy": (
                "PostgreSQL is the only distributed state truth",
                "Redis queue and replay are delivery projections only",
                "Worker commits state before ACK",
                "Artifact bytes remain outside messages and traces",
                "deployment owns credentials, migrations, and physical isolation",
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
