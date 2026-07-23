from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agentos.deployment_constants import (
    PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
)
from agentos.deployment_profiles import (
    DeploymentLiveBackendVerificationProfile,
    ProductionStatePlaneDeploymentProfile,
)
from agentos.readiness import ProductionReadinessEvidenceBundle


REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "postgres_state_store",
    "postgres_artifact_store",
    "redis_worker_queue",
    "redis_relay_queue",
    "redis_event_replay",
    "distributed_worker",
    "distributed_runtime_profile",
    "channel_services",
    "distributed_asgi_app",
    "production_state_plane_profile",
    "live_backend_verification",
    "production_readiness_evidence_bundle",
)

_SECRET_KEY_PARTS = frozenset(
    {
        "api",
        "credential",
        "credentials",
        "key",
        "password",
        "secret",
        "token",
    },
)


@dataclass(frozen=True, slots=True)
class ReferenceStatePlaneStackProfile:
    """Readiness contract for the SDK reference state-plane composition."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS
    )
    probe_name: str = "reference_state_plane_stack"

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
        """Return required reference stack components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe metadata for the reference state-plane stack."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "ReferenceStatePlaneStack",
                "ReferenceStatePlaneStackProfile",
                "REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS",
                "reference state plane",
                "readiness source aggregation",
                "component identity evidence",
                "JSON-safe audit evidence",
                "does not create backend clients",
            ),
            "deployment_owned": (
                "credentials",
                "migrations",
                "backend creation",
                "live probes",
                "autoscaling",
                "tenant directory",
                "CI matrix execution",
                "rollout",
                "alert routing",
                "runbooks",
                (
                    "credentials, migrations, CI matrix execution, alert "
                    "routing and runbooks remain deployment-owned"
                ),
            ),
            "boundary_policy": (
                "PostgreSQL is the only distributed state truth",
                "Redis queue and replay are delivery projections only",
                "DistributedWorker commits state before ACK",
                "ChannelServices exposes application services to stateless channels",
                "DistributedAsgiApp maps ASGI wire traffic without owning state",
                "ProductionReadinessEvidenceBundle gates release evidence",
                "does not create backend clients",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible profile check."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
            "block_production_readiness": not ok,
        }

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


@dataclass(slots=True)
class ReferenceStatePlaneStack:
    """SDK-owned reference composition over deployment-owned state backends."""

    postgres_state_store: object | None = None
    postgres_artifact_store: object | None = None
    redis_worker_queue: object | None = None
    redis_relay_queue: object | None = None
    redis_event_replay: object | None = None
    distributed_worker: object | None = None
    runtime_profile: object | None = None
    channel_services: object | None = None
    asgi_app: object | None = None
    state_plane_profile: object | None = None
    live_backend_verification: object | None = None
    readiness_bundle: ProductionReadinessEvidenceBundle | None = None
    stack_profile: ReferenceStatePlaneStackProfile | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def configured_components(self) -> tuple[str, ...]:
        """Return configured reference stack component names."""

        component_values = {
            "postgres_state_store": self.postgres_state_store,
            "postgres_artifact_store": self.postgres_artifact_store,
            "redis_worker_queue": self.redis_worker_queue,
            "redis_relay_queue": self.redis_relay_queue,
            "redis_event_replay": self.redis_event_replay,
            "distributed_worker": self.distributed_worker,
            "distributed_runtime_profile": self.runtime_profile,
            "channel_services": self.channel_services,
            "distributed_asgi_app": self.asgi_app,
            "production_state_plane_profile": self.state_plane_profile,
            "live_backend_verification": self.live_backend_verification,
        }
        configured = [
            name
            for name, value in component_values.items()
            if value is not None
        ]
        configured.append("production_readiness_evidence_bundle")
        return tuple(
            name
            for name in REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS
            if name in configured
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required reference stack components not configured."""

        return self._stack_profile().missing_components()

    def readiness_sources(self) -> dict[str, object]:
        """Return readiness sources consumed by the release evidence bundle."""

        sources: dict[str, object] = {
            "reference_state_plane_stack": self._stack_profile(),
        }
        self._add_source(
            sources,
            "production_state_plane",
            self.state_plane_profile,
        )
        self._add_source(
            sources,
            "distributed_runtime_profile",
            self.runtime_profile,
        )
        self._add_source(
            sources,
            "distributed_worker",
            self.distributed_worker,
        )
        self._add_source(
            sources,
            "channel_services",
            self.channel_services,
        )
        self._add_source(
            sources,
            "distributed_asgi_app",
            self.asgi_app,
        )
        self._add_source(
            sources,
            "live_backend_verification",
            self.live_backend_verification,
        )
        if self.readiness_bundle is not None:
            sources["production_readiness_evidence_bundle"] = self.readiness_bundle
        return sources

    def build_readiness_bundle(self) -> ProductionReadinessEvidenceBundle:
        """Aggregate configured readiness evidence into a release gate bundle."""

        if self.readiness_bundle is not None:
            return self.readiness_bundle
        return ProductionReadinessEvidenceBundle.from_sources(
            self.readiness_sources(),
            required_checks=(
                "reference_state_plane_stack",
                "production_state_plane",
                "distributed_runtime_profile",
                "distributed_worker",
                "channel_services",
                "distributed_asgi_app",
                "live_backend_verification",
            ),
            bundle_name="reference_state_plane",
            metadata={
                "profile": self.__class__.__name__,
                "reference_state_plane": True,
                "configured_components": self.configured_components(),
                "missing_components": self.missing_components(),
                "component_identities": self.component_identities(),
                "sdk_owned": (
                    "ReferenceStatePlaneStack",
                    "ProductionReadinessEvidenceBundle",
                    "readiness source aggregation",
                    "component identity evidence",
                    "JSON-safe audit evidence",
                    "does not create backend clients",
                ),
                "deployment_owned": (
                    "credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned",
                ),
            },
        )

    def component_identities(self) -> dict[str, str | None]:
        """Return JSON-safe component class identities for audit evidence."""

        return {
            "postgres_state_store": _adapter_name(self.postgres_state_store),
            "postgres_artifact_store": _adapter_name(
                self.postgres_artifact_store,
            ),
            "redis_worker_queue": _adapter_name(self.redis_worker_queue),
            "redis_relay_queue": _adapter_name(self.redis_relay_queue),
            "redis_event_replay": _adapter_name(self.redis_event_replay),
            "distributed_worker": _adapter_name(self.distributed_worker),
            "runtime_profile": _adapter_name(self.runtime_profile),
            "channel_services": _adapter_name(self.channel_services),
            "asgi_app": _adapter_name(self.asgi_app),
            "state_plane_profile": _adapter_name(self.state_plane_profile),
            "live_backend_verification": _adapter_name(
                self.live_backend_verification,
            ),
            "readiness_bundle": (
                "ProductionReadinessEvidenceBundle"
                if self.readiness_bundle is None
                else _adapter_name(self.readiness_bundle)
            ),
        }

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe reference stack readiness metadata."""

        bundle = self.build_readiness_bundle()
        payload = {
            "profile": self.__class__.__name__,
            "probe_name": "reference_state_plane_stack",
            "ready": bundle.accepted,
            "configured_components": self.configured_components(),
            "missing_components": self.missing_components(),
            "component_identities": self.component_identities(),
            "state_plane_profile": self._source_metadata(self.state_plane_profile),
            "runtime_profile": self._source_metadata(self.runtime_profile),
            "distributed_worker": self._source_metadata(self.distributed_worker),
            "channel_services": self._source_metadata(self.channel_services),
            "asgi_app": self._source_metadata(self.asgi_app),
            "live_backend_verification": self._source_metadata(
                self.live_backend_verification,
            ),
            "readiness_bundle": bundle.as_dict(),
            "metadata": _json_safe_value(self.metadata),
            "sdk_owned": (
                "ReferenceStatePlaneStack",
                "ReferenceStatePlaneStackProfile",
                "REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS",
                "ProductionStatePlaneDeploymentProfile",
                "DeploymentLiveBackendVerificationProfile",
                "ProductionReadinessEvidenceBundle",
                "DistributedRuntimeProfile",
                "DistributedWorker",
                "ChannelServices",
                "DistributedAsgiApp",
                "PostgresStateStore",
                "PostgresArtifactStore",
                "RedisQueueAdapter",
                "RedisEventReplayAdapter",
                "reference state plane",
                "readiness source aggregation",
                "component identity evidence",
                "JSON-safe audit evidence",
                "does not create backend clients",
            ),
            "deployment_owned": (
                "PostgreSQL and Redis deployment and credentials",
                "distributed runtime migration execution",
                "shared BlobStore deployment",
                "worker process hosting and autoscaling",
                "live backend probe execution",
                "tenant directory integration",
                "credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned",
            ),
        }
        return _json_safe_mapping(payload)

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible reference stack check."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
            "block_production_readiness": not ok,
        }

    def as_dict(self) -> dict[str, object]:
        """Return JSON-safe audit evidence for this reference stack."""

        return self.readiness_metadata()

    def _stack_profile(self) -> ReferenceStatePlaneStackProfile:
        if self.stack_profile is not None:
            return self.stack_profile
        return ReferenceStatePlaneStackProfile(
            configured_components=self.configured_components(),
        )

    def _source_metadata(self, source: object | None) -> object | None:
        if source is None:
            return None
        for method_name in ("readiness_metadata", "as_dict"):
            method = getattr(source, method_name, None)
            if callable(method):
                return method()
        return {"profile": _adapter_name(source)}

    def _add_source(
        self,
        sources: dict[str, object],
        name: str,
        source: object | None,
    ) -> None:
        if source is not None:
            sources[name] = source


def _adapter_name(adapter: object | None) -> str | None:
    if adapter is None:
        return None
    return adapter.__class__.__name__


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _json_safe_value(value, key_hint=str(key))
        for key, value in values.items()
    }


def _json_safe_value(value: object, *, key_hint: str = "") -> object:
    if _is_secret_like_key(key_hint):
        return "<redacted>"
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple | list):
        return tuple(_json_safe_value(item) for item in value)
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return repr(value)


def _is_secret_like_key(key: str) -> bool:
    lowered = key.lower()
    parts = {
        part
        for part in lowered.replace("-", "_").replace(".", "_").split("_")
        if part
    }
    if lowered in _SECRET_KEY_PARTS:
        return True
    return bool(parts & _SECRET_KEY_PARTS)


def _default_state_plane_profile() -> ProductionStatePlaneDeploymentProfile:
    return ProductionStatePlaneDeploymentProfile(
        configured_components=PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
    )


def _default_live_backend_verification() -> DeploymentLiveBackendVerificationProfile:
    return DeploymentLiveBackendVerificationProfile()


__all__ = [
    "REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS",
    "ReferenceStatePlaneStack",
    "ReferenceStatePlaneStackProfile",
]
