from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agentos.deployment_constants import (
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
)
from agentos.deployment_reports import DeploymentLiveBackendVerificationRunResult
from agentos.deployment_validation import BackendVerificationInvocationPlan
from agentos.readiness import ProductionReadinessEvidenceBundle


REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME = "reference_live_backend_probe_pack"

_RESTRICTED_METADATA_KEY_PARTS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "connection_string",
    "connection_uri",
    "credential",
    "dsn",
    "env",
    "password",
    "private_key",
    "raw_config",
    "secret",
    "token",
)


def _default_probe_specs() -> tuple[ReferenceLiveBackendProbeSpec, ...]:
    return (
        ReferenceLiveBackendProbeSpec(
            backend_name="postgres_state_store",
            backend_kind="postgres",
            adapter_hint="PostgresStateStore",
        ),
        ReferenceLiveBackendProbeSpec(
            backend_name="postgres_artifact_store",
            backend_kind="postgres",
            adapter_hint="PostgresArtifactStore",
        ),
        ReferenceLiveBackendProbeSpec(
            backend_name="redis_worker_queue",
            backend_kind="redis",
            adapter_hint="RedisQueueAdapter",
        ),
        ReferenceLiveBackendProbeSpec(
            backend_name="redis_relay_queue",
            backend_kind="redis",
            adapter_hint="RedisQueueAdapter",
        ),
        ReferenceLiveBackendProbeSpec(
            backend_name="redis_event_replay",
            backend_kind="redis",
            adapter_hint="RedisEventReplayAdapter",
        ),
        ReferenceLiveBackendProbeSpec(
            backend_name="distributed_worker",
            backend_kind="distributed_worker",
            adapter_hint="DistributedWorker",
        ),
    )


@dataclass(frozen=True, slots=True)
class ReferenceLiveBackendProbeSpec:
    """One SDK reference probe declaration for deployment-owned backend checks."""

    backend_name: str
    backend_kind: str
    adapter_hint: str
    probe_name: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_name(self.backend_name, field_name="backend_name")
        _validate_name(self.backend_kind, field_name="backend_kind")
        _validate_name(self.adapter_hint, field_name="adapter_hint")
        if self.probe_name is not None:
            _validate_name(self.probe_name, field_name="probe_name")
        _reject_restricted_metadata_keys(self.metadata)

    @property
    def stable_probe_name(self) -> str:
        """Return the stable probe name used by generated invocation plans."""

        return self.probe_name or self.backend_name

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe probe declaration."""

        return {
            "backend_name": self.backend_name,
            "backend_kind": self.backend_kind,
            "adapter_hint": self.adapter_hint,
            "probe_name": self.stable_probe_name,
            "metadata": _json_safe_mapping(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ReferenceLiveBackendProbePack:
    """Reference state-plane probe pack over deployment-owned check scripts."""

    probe_specs: tuple[ReferenceLiveBackendProbeSpec, ...] = field(
        default_factory=_default_probe_specs,
    )
    command_prefix: tuple[str, ...] = (
        "python",
        "-m",
        "agentos.examples.live_backend_probe",
    )
    pack_name: str = REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME
    environment: str | None = None
    artifact_uri_prefix: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_name(self.pack_name, field_name="pack_name")
        if not self.command_prefix:
            raise ValueError("command_prefix must not be empty")
        for value in self.command_prefix:
            _validate_name(value, field_name="command_prefix")
            if _looks_like_shell_string(value):
                raise ValueError("command_prefix must be argv-only")
        if not self.probe_specs:
            raise ValueError("probe_specs must not be empty")
        _validate_unique_probe_specs(self.probe_specs)
        if self.required_backends != LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS:
            raise ValueError(
                "probe_specs must cover LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS",
            )
        if self.environment is not None:
            _validate_name(self.environment, field_name="environment")
        if self.artifact_uri_prefix is not None:
            _validate_name(
                self.artifact_uri_prefix,
                field_name="artifact_uri_prefix",
            )
        _reject_restricted_metadata_keys(self.metadata)

    @property
    def required_backends(self) -> tuple[str, ...]:
        """Return the required state-plane backend names in probe order."""

        return tuple(spec.backend_name for spec in self.probe_specs)

    def probe_by_backend(self, backend_name: str) -> ReferenceLiveBackendProbeSpec:
        """Return the reference probe declaration for a backend."""

        for spec in self.probe_specs:
            if spec.backend_name == backend_name:
                return spec
        raise KeyError(f"unknown backend probe: {backend_name}")

    def invocation_plan(self, backend_name: str) -> BackendVerificationInvocationPlan:
        """Build an argv-only invocation plan for one backend probe."""

        spec = self.probe_by_backend(backend_name)
        metadata: dict[str, object] = {
            "probe_pack": self.pack_name,
            "probe_name": spec.stable_probe_name,
            "backend_kind": spec.backend_kind,
            "adapter_hint": spec.adapter_hint,
            "does not create backend clients": True,
        }
        metadata.update(_json_safe_mapping(self.metadata))
        metadata.update(_json_safe_mapping(spec.metadata))
        if self.environment is not None:
            metadata["deployment_stage"] = self.environment
        artifact_uri = self._artifact_uri(spec)
        if artifact_uri is not None:
            metadata["artifact_uri"] = artifact_uri
        return BackendVerificationInvocationPlan(
            command=(*self.command_prefix, spec.stable_probe_name),
            required_backends=(spec.backend_name,),
            metadata=metadata,
        )

    def invocation_plans(self) -> tuple[BackendVerificationInvocationPlan, ...]:
        """Build invocation plans for every required backend probe."""

        return tuple(
            self.invocation_plan(spec.backend_name)
            for spec in self.probe_specs
        )

    def readiness_bundle(
        self,
        run_results: Mapping[str, DeploymentLiveBackendVerificationRunResult],
    ) -> ProductionReadinessEvidenceBundle:
        """Aggregate backend run evidence into a production readiness bundle."""

        sources = {
            backend_name: result.as_dict()
            for backend_name, result in run_results.items()
        }
        return ProductionReadinessEvidenceBundle.from_sources(
            sources,
            required_checks=self.required_backends,
            bundle_name=self.pack_name,
            metadata={
                "probe_pack": self.pack_name,
                "readiness source aggregation": True,
                "component identity evidence": True,
                "does not create backend clients": True,
                "sdk_owned": (
                    "ReferenceLiveBackendProbePack",
                    "ReferenceLiveBackendProbeSpec",
                    "BackendVerificationInvocationPlan",
                    "DeploymentLiveBackendVerificationRunResult",
                    "ProductionReadinessEvidenceBundle",
                ),
                "deployment_owned": (
                    "PostgreSQL state and Artifact probe implementation",
                    "Redis queue and replay probe implementation",
                    "Distributed Worker probe implementation",
                    "shared BlobStore probe implementation",
                    "credentials, migrations, CI matrix execution, alert routing and runbooks",
                ),
            },
        )

    def as_dict(self) -> dict[str, object]:
        """Return JSON-safe probe pack evidence and ownership metadata."""

        return {
            "pack_name": self.pack_name,
            "required_backends": self.required_backends,
            "command_prefix": self.command_prefix,
            "environment": self.environment,
            "artifact_uri_prefix": self.artifact_uri_prefix,
            "probe_specs": tuple(spec.as_dict() for spec in self.probe_specs),
            "metadata": _json_safe_mapping(self.metadata),
            "sdk_owned": (
                "ReferenceLiveBackendProbePack",
                "ReferenceLiveBackendProbeSpec",
                "BackendVerificationInvocationPlan",
                "ProductionReadinessEvidenceBundle",
                "argv-only invocation plan",
                "readiness source aggregation",
                "component identity evidence",
                "does not create backend clients",
            ),
            "deployment_owned": (
                "PostgreSQL state and Artifact probe implementation",
                "Redis queue and replay probe implementation",
                "Distributed Worker probe implementation",
                "shared BlobStore probe implementation",
                "credentials, migrations, CI matrix execution, alert routing and runbooks",
            ),
        }

    def _artifact_uri(self, spec: ReferenceLiveBackendProbeSpec) -> str | None:
        if self.artifact_uri_prefix is None:
            return None
        return f"{self.artifact_uri_prefix.rstrip('/')}/{spec.backend_name}.json"


def _validate_unique_probe_specs(
    specs: tuple[ReferenceLiveBackendProbeSpec, ...],
) -> None:
    backend_names = tuple(spec.backend_name for spec in specs)
    if len(set(backend_names)) != len(backend_names):
        raise ValueError("probe_specs must not contain duplicate backend names")


def _validate_name(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _looks_like_shell_string(value: str) -> bool:
    if len(value.split()) <= 1:
        return False
    return not value.startswith(("http://", "https://", "s3://", "ci://"))


def _reject_restricted_metadata_keys(
    values: Mapping[str, object],
    *,
    path: str = "metadata",
) -> None:
    for key, value in values.items():
        lowered = str(key).lower()
        if any(part in lowered for part in _RESTRICTED_METADATA_KEY_PARTS):
            raise ValueError(f"metadata contains restricted key: {path}.{key}")
        if isinstance(value, Mapping):
            _reject_restricted_metadata_keys(value, path=f"{path}.{key}")


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _json_safe_value(value) for key, value in values.items()}


def _json_safe_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return tuple(_json_safe_value(item) for item in value)
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return repr(value)


__all__ = [
    "REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME",
    "ReferenceLiveBackendProbePack",
    "ReferenceLiveBackendProbeSpec",
]
