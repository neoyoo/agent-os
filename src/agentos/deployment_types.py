from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agentos._redaction import redact_command_argv, redact_secret_patterns
from agentos.deployment_constants import (
    _BACKEND_VERIFICATION_STATUSES,
    _RESTRICTED_EVIDENCE_METADATA_KEYS,
    BackendVerificationStatus,
    WorkerProcessKind,
    WorkerProcessStatus,
)


@dataclass(frozen=True, slots=True)
class BackendVerificationRecord:
    """JSON-safe evidence for a deployment-owned live backend check."""

    backend_name: str
    backend_kind: str
    status: BackendVerificationStatus
    checked_at: float
    evidence_ref: str
    target_ref: str | None = None
    error: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.backend_name.strip():
            raise ValueError("backend_name must not be empty")
        if not self.backend_kind.strip():
            raise ValueError("backend_kind must not be empty")
        if self.status not in _BACKEND_VERIFICATION_STATUSES:
            raise ValueError("status must be passed, failed, skipped, or unknown")
        if not self.evidence_ref.strip():
            raise ValueError("evidence_ref must not be empty")
        if self.target_ref is not None and not self.target_ref.strip():
            raise ValueError("target_ref must not be empty")
        if self.error is not None and not self.error.strip():
            raise ValueError("error must not be empty")
        _reject_restricted_metadata_keys(self.metadata)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe verification record payload."""

        return {
            "backend_name": self.backend_name,
            "backend_kind": self.backend_kind,
            "status": self.status,
            "checked_at": self.checked_at,
            "evidence_ref": redact_secret_patterns(self.evidence_ref),
            "target_ref": (
                None
                if self.target_ref is None
                else redact_secret_patterns(self.target_ref)
            ),
            "error": None if self.error is None else redact_secret_patterns(self.error),
            "metadata": _json_safe_mapping(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class WorkerProcessSpec:
    """Stable local worker process launch spec.

    The command is argv-only. Shell parsing, restart policy, secrets,
    autoscaling, and production service management stay deployment-owned.
    """

    worker_id: str
    command: tuple[str, ...]
    worker_kind: WorkerProcessKind = "worker"
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if not self.worker_kind.strip():
            raise ValueError("worker_kind must not be empty")
        if not self.command:
            raise ValueError("command must not be empty")
        if any(not item.strip() for item in self.command):
            raise ValueError("command must not contain empty values")
        if self.cwd is not None and not self.cwd.strip():
            raise ValueError("cwd must not be empty")
        if any(not key.strip() for key in self.env):
            raise ValueError("env must not contain empty names")
        _reject_restricted_metadata_keys(self.metadata)


@dataclass(frozen=True, slots=True)
class WorkerProcessState:
    """Immutable evidence snapshot for one local worker process."""

    worker_id: str
    worker_kind: WorkerProcessKind
    command: tuple[str, ...]
    status: WorkerProcessStatus
    cwd: str | None = None
    env_keys: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    pid: int | None = None
    started_at: float | None = None
    last_heartbeat_at: float | None = None
    stop_requested_at: float | None = None
    stopped_at: float | None = None
    exit_code: int | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _reject_restricted_metadata_keys(self.metadata)

    @classmethod
    def from_spec(
        cls,
        spec: WorkerProcessSpec,
        *,
        status: WorkerProcessStatus = "configured",
        pid: int | None = None,
        started_at: float | None = None,
        last_heartbeat_at: float | None = None,
        stop_requested_at: float | None = None,
        stopped_at: float | None = None,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> WorkerProcessState:
        """Create a state snapshot from a launch spec."""

        return cls(
            worker_id=spec.worker_id,
            worker_kind=spec.worker_kind,
            command=tuple(spec.command),
            status=status,
            cwd=spec.cwd,
            env_keys=tuple(sorted(spec.env)),
            metadata=dict(spec.metadata),
            pid=pid,
            started_at=started_at,
            last_heartbeat_at=last_heartbeat_at,
            stop_requested_at=stop_requested_at,
            stopped_at=stopped_at,
            exit_code=exit_code,
            error=error,
        )

    def to_evidence(self) -> dict[str, object]:
        """Return JSON-safe lifecycle evidence without environment values."""

        return {
            "worker_id": self.worker_id,
            "worker_kind": self.worker_kind,
            "command": redact_command_argv(self.command),
            "status": self.status,
            "cwd": self.cwd,
            "env_keys": self.env_keys,
            "metadata": _json_safe_mapping(self.metadata),
            "pid": self.pid,
            "started_at": self.started_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "stop_requested_at": self.stop_requested_at,
            "stopped_at": self.stopped_at,
            "exit_code": self.exit_code,
            "error": self.error,
        }


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _json_safe_value(value) for key, value in values.items()}


def _validate_non_empty_names(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain empty names")


def _validate_optional_text(value: str | None, *, field_name: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _reject_restricted_metadata_keys(
    values: Mapping[str, object],
    *,
    path: str = "metadata",
    allowed_keys: tuple[str, ...] = (),
) -> None:
    for key, value in values.items():
        if str(key) in allowed_keys:
            continue
        lowered = str(key).lower()
        if any(restricted in lowered for restricted in _RESTRICTED_EVIDENCE_METADATA_KEYS):
            raise ValueError(f"metadata contains restricted key: {path}.{key}")
        if isinstance(value, Mapping):
            _reject_restricted_metadata_keys(
                value,
                path=f"{path}.{key}",
                allowed_keys=allowed_keys,
            )


def _json_safe_value(value: object) -> object:
    if isinstance(value, str):
        return redact_secret_patterns(value)
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return tuple(_json_safe_value(item) for item in value)
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return repr(value)


__all__ = [
    "BackendVerificationRecord",
    "WorkerProcessSpec",
    "WorkerProcessState",
]
