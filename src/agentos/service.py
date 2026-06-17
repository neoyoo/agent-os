from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "asgi_agent_app",
    "runtime_profile",
    "session_provider",
    "snapshot_persistence",
    "workspace_execution_backend",
    "auth_policy",
    "rate_limiter",
    "readiness_endpoint",
    "health_endpoint",
    "state_plane_profile",
    "distributed_session_profile",
    "workspace_isolation_profile",
    "live_backend_verification",
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


class ServiceRuntimeProfile(Protocol):
    """Runtime profile shape consumed by the reference service layer."""

    session_provider: object


@dataclass(frozen=True, slots=True)
class AgentServiceReferenceProfile:
    """Readiness contract for the Agent Service reference composition."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS
    )
    probe_name: str = "agent_service_reference"

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
        """Return required service components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe readiness metadata for the service reference."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "AgentServiceReference",
                "AgentServiceReferenceProfile",
                "AsgiAgentApp composition",
                "DistributedWebRuntimeProfile injection",
                "readiness check aggregation",
                "auth/rate-limit hook injection",
                "JSON-safe readiness evidence",
            ),
            "deployment_owned": (
                "provider credentials",
                "Redis/Postgres/Nacos credentials and migrations",
                "gateway, TLS, CORS, WAF, and tenant directory integration",
                "distributed/global rate-limit stores",
                "sandbox image/runtime patching",
                "Kubernetes/systemd/autoscaling",
                "CI/CD, rollout, rollback, alerting, and runbooks",
                "live backend verification execution",
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


@dataclass(slots=True)
class AgentServiceReference:
    """Reference ASGI hosting composition for production web agents."""

    runtime_profile: ServiceRuntimeProfile
    service_profile: AgentServiceReferenceProfile = field(
        default_factory=AgentServiceReferenceProfile,
    )
    distributed_session_profile: object | None = None
    state_plane_profile: object | None = None
    workspace_isolation_profile: object | None = None
    workspace_execution_backend: object | None = None
    auth_policy: object | None = None
    rate_limiter: object | None = None
    readiness_checks: Mapping[str, Callable[[], object]] = field(
        default_factory=dict,
    )
    health_checks: Mapping[str, Callable[[], object]] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def build_asgi_app(self) -> object:
        """Build the reference ASGI app using the existing channel app."""

        from agentos.channels import AsgiAgentApp

        runtime_readiness_checks = getattr(
            self.runtime_profile,
            "readiness_checks",
            None,
        )
        runtime_health_checks = getattr(
            self.runtime_profile,
            "health_checks",
            None,
        )

        return AsgiAgentApp(
            sessions=self.runtime_profile.session_provider,
            auth_policy=(
                self.auth_policy
                if self.auth_policy is not None
                else getattr(self.runtime_profile, "auth_policy", None)
            ),
            rate_limiter=(
                self.rate_limiter
                if self.rate_limiter is not None
                else getattr(self.runtime_profile, "rate_limiter", None)
            ),
            readiness_checks={
                **dict(runtime_readiness_checks or {}),
                **self._aggregated_readiness_checks(),
            },
            health_checks={
                **dict(runtime_health_checks or {}),
                **dict(self.health_checks),
            },
            a2a_server=getattr(self.runtime_profile, "a2a_server", None),
            sse_event_buffer=getattr(self.runtime_profile, "sse_event_buffer", None),
            sse_turn_control=getattr(self.runtime_profile, "sse_turn_control", None),
            sse_turn_control_owner_id=getattr(
                self.runtime_profile,
                "sse_turn_control_owner_id",
                None,
            ),
            sse_turn_control_ttl_seconds=getattr(
                self.runtime_profile,
                "sse_turn_control_ttl_seconds",
                60.0,
            ),
            sse_turn_control_poll_interval_seconds=getattr(
                self.runtime_profile,
                "sse_turn_control_poll_interval_seconds",
                None,
            ),
            session_lease_heartbeat_interval_seconds=getattr(
                self.runtime_profile,
                "session_lease_heartbeat_interval_seconds",
                None,
            ),
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe evidence for this service composition."""

        payload = {
            "profile": self.__class__.__name__,
            "runtime_profile": _adapter_name(self.runtime_profile),
            "session_provider": _adapter_name(
                getattr(self.runtime_profile, "session_provider", None),
            ),
            "snapshot_persistence": _adapter_name(
                getattr(self.runtime_profile, "snapshot_persistence", None),
            ),
            "workspace_execution_backend": _adapter_name(
                self.workspace_execution_backend,
            ),
            "auth_policy": _adapter_name(self.auth_policy),
            "rate_limiter": _adapter_name(self.rate_limiter),
            "service_profile": self.service_profile.readiness_metadata(),
            "distributed_session_profile": self._child_metadata(
                self.distributed_session_profile,
            ),
            "state_plane_profile": self._child_metadata(self.state_plane_profile),
            "workspace_isolation_profile": self._child_metadata(
                self.workspace_isolation_profile,
            ),
            "metadata": _json_safe_value(self.metadata),
            "sdk_owned": (
                "AsgiAgentApp",
                "DistributedWebRuntimeProfile",
                "DurableAgentSessionProvider",
                "SessionPersistence",
                "WorkspaceExecutionBackend",
                "ChannelAuthPolicy",
                "RateLimiter",
                "readiness endpoint composition",
            ),
            "deployment_owned": (
                "gateway/TLS/CORS/WAF",
                "provider and backend credentials",
                "tenant directory and RBAC lifecycle",
                "Kubernetes/systemd/autoscaling",
                "sandbox image/runtime patching",
                "live backend verification",
            ),
        }
        return _json_safe_mapping(payload)

    def readiness_check(self) -> dict[str, object]:
        """Return one service-level ASGI readiness check payload."""

        metadata = self.readiness_metadata()
        child_checks = [
            self.service_profile.readiness_check(),
            self._child_check(self.distributed_session_profile),
            self._child_check(self.state_plane_profile),
            self._child_check(self.workspace_isolation_profile),
        ]
        ok = all(
            bool(check.get("ok"))
            if isinstance(check, Mapping) and "ok" in check
            else (
                check.get("status") in {"ok", "ready", True}
                if isinstance(check, Mapping)
                else bool(check)
            )
            for check in child_checks
            if check is not None
        )
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _aggregated_readiness_checks(self) -> dict[str, Callable[[], object]]:
        checks: dict[str, Callable[[], object]] = {
            self.service_profile.probe_name: self.readiness_check,
        }
        self._add_profile_readiness_check(checks, self.distributed_session_profile)
        self._add_profile_readiness_check(checks, self.state_plane_profile)
        self._add_profile_readiness_check(checks, self.workspace_isolation_profile)
        checks.update(dict(self.readiness_checks))
        return checks

    def _add_profile_readiness_check(
        self,
        checks: dict[str, Callable[[], object]],
        profile: object | None,
    ) -> None:
        if profile is None:
            return
        readiness_check = getattr(profile, "readiness_check", None)
        if not callable(readiness_check):
            return
        probe_name = str(
            getattr(profile, "probe_name", profile.__class__.__name__),
        )
        checks[probe_name] = readiness_check

    def _child_metadata(self, profile: object | None) -> object | None:
        if profile is None:
            return None
        readiness_metadata = getattr(profile, "readiness_metadata", None)
        if callable(readiness_metadata):
            return readiness_metadata()
        return {"profile": _adapter_name(profile)}

    def _child_check(self, profile: object | None) -> object | None:
        if profile is None:
            return None
        readiness_check = getattr(profile, "readiness_check", None)
        if callable(readiness_check):
            return readiness_check()
        return True


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
        return [_json_safe_value(item) for item in value]
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


__all__ = [
    "AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS",
    "AgentServiceReference",
    "AgentServiceReferenceProfile",
]
