from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol

from agentos._redaction import redact_command_argv


BackendVerificationStatus = Literal["passed", "failed", "skipped", "unknown"]

WorkerProcessKind = Literal[
    "worker",
    "team_worker",
    "planner_worker",
    "a2a_push_worker",
]
WorkerProcessStatus = Literal[
    "configured",
    "running",
    "stopping",
    "stopped",
    "exited",
    "failed",
]


PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "agent_registry",
    "message_queue",
    "task_store",
    "plan_store",
    "worker_process_supervisor",
    "session_snapshot_persistence",
    "state_plane_boundary_policy",
    "live_backend_verification",
)


LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS: tuple[str, ...] = (
    "agent_registry",
    "message_queue",
    "task_store",
    "plan_store",
    "worker_process_supervisor",
    "session_snapshot_persistence",
)

_BACKEND_VERIFICATION_STATUSES: tuple[str, ...] = (
    "passed",
    "failed",
    "skipped",
    "unknown",
)

_RESTRICTED_EVIDENCE_METADATA_KEYS: tuple[str, ...] = (
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
            "evidence_ref": self.evidence_ref,
            "target_ref": self.target_ref,
            "error": self.error,
            "metadata": _json_safe_mapping(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class DeploymentLiveBackendVerificationGateReport:
    """Release/readiness gate over deployment-owned backend verification evidence."""

    required_backends: tuple[str, ...]
    records: tuple[BackendVerificationRecord, ...]
    missing_backends: tuple[str, ...]
    failed_backends: tuple[str, ...]
    skipped_backends: tuple[str, ...]
    unknown_backends: tuple[str, ...]
    accepted: bool
    block_production_readiness: bool

    @classmethod
    def from_records(
        cls,
        records: tuple[BackendVerificationRecord, ...],
        *,
        required_backends: tuple[str, ...] = (
            LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
        ),
    ) -> DeploymentLiveBackendVerificationGateReport:
        """Build a gate report from externally produced backend check records."""

        if not required_backends:
            raise ValueError("required_backends must not be empty")
        if any(not backend.strip() for backend in required_backends):
            raise ValueError("required_backends must not contain empty names")
        by_name = {record.backend_name: record for record in records}
        missing = tuple(
            backend for backend in required_backends if backend not in by_name
        )
        failed = tuple(
            backend
            for backend in required_backends
            if by_name.get(backend) is not None
            and by_name[backend].status == "failed"
        )
        skipped = tuple(
            backend
            for backend in required_backends
            if by_name.get(backend) is not None
            and by_name[backend].status == "skipped"
        )
        unknown = tuple(
            backend
            for backend in required_backends
            if by_name.get(backend) is not None
            and by_name[backend].status == "unknown"
        )
        accepted = not (missing or failed or skipped or unknown)
        return cls(
            required_backends=tuple(required_backends),
            records=tuple(records),
            missing_backends=missing,
            failed_backends=failed,
            skipped_backends=skipped,
            unknown_backends=unknown,
            accepted=accepted,
            block_production_readiness=not accepted,
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe gate report payload."""

        return {
            "status": "ok" if self.accepted else "failed",
            "accepted": self.accepted,
            "block_production_readiness": self.block_production_readiness,
            "required_backends": self.required_backends,
            "missing_backends": self.missing_backends,
            "failed_backends": self.failed_backends,
            "skipped_backends": self.skipped_backends,
            "unknown_backends": self.unknown_backends,
            "records": tuple(record.as_dict() for record in self.records),
            "sdk_owned": (
                "BackendVerificationRecord",
                "DeploymentLiveBackendVerificationGateReport",
                "required backend evidence coverage checks",
                "JSON-safe verification evidence payloads",
            ),
            "deployment_owned": (
                "backend check execution",
                "credentials and secret distribution",
                "network and TLS policy",
                "migration execution",
                "CI matrix execution",
                "alert routing and runbooks",
                "release approval and certification",
            ),
        }


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


class BackendVerificationReportImportError(ValueError):
    """Raised when external backend verification evidence cannot be imported."""


class BackendVerificationReportImporter:
    """Import deployment-owned backend verification reports into SDK records."""

    def from_json(self, payload: str) -> tuple[BackendVerificationRecord, ...]:
        """Parse a JSON report payload containing backend verification records."""

        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise BackendVerificationReportImportError(
                f"invalid JSON backend verification report: {error.msg}",
            ) from error
        if not isinstance(loaded, Mapping):
            raise BackendVerificationReportImportError(
                "backend verification report must be a JSON object",
            )
        return self.from_mapping(loaded)

    def from_mapping(
        self,
        payload: Mapping[str, object],
    ) -> tuple[BackendVerificationRecord, ...]:
        """Normalize a mapping payload into backend verification records."""

        records = payload.get("records")
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise BackendVerificationReportImportError(
                "backend verification report records must be a list",
            )
        if not records:
            raise BackendVerificationReportImportError(
                "backend verification report records must not be empty",
            )
        return tuple(
            self._record_from_mapping(item, index=index)
            for index, item in enumerate(records)
        )

    def _record_from_mapping(
        self,
        value: object,
        *,
        index: int,
    ) -> BackendVerificationRecord:
        if not isinstance(value, Mapping):
            raise BackendVerificationReportImportError(
                f"backend verification record {index} must be an object",
            )
        backend_name = self._string_field(
            value,
            "backend_name",
            "backendName",
            "name",
        )
        if backend_name is None:
            raise BackendVerificationReportImportError(
                f"backend verification record {index} is missing backend_name",
            )
        backend_kind = self._string_field(
            value,
            "backend_kind",
            "backendKind",
            "kind",
            "type",
        )
        if backend_kind is None:
            raise BackendVerificationReportImportError(
                f"backend verification record {index} is missing backend_kind",
            )
        status = self._status_field(value, index=index)
        checked_at = self._number_field(value, "checked_at", "checkedAt")
        if checked_at is None:
            raise BackendVerificationReportImportError(
                f"backend verification record {index} is missing checked_at",
            )
        evidence_ref = self._string_field(
            value,
            "evidence_ref",
            "evidenceRef",
            "evidence",
        )
        if evidence_ref is None:
            raise BackendVerificationReportImportError(
                f"backend verification record {index} is missing evidence_ref",
            )
        return BackendVerificationRecord(
            backend_name=backend_name,
            backend_kind=backend_kind,
            status=status,
            checked_at=checked_at,
            evidence_ref=evidence_ref,
            target_ref=self._string_field(value, "target_ref", "targetRef", "target"),
            error=self._string_field(value, "error", "detail", "message"),
            metadata=self._metadata(value.get("metadata")),
        )

    def _string_field(
        self,
        payload: Mapping[str, object],
        *names: str,
    ) -> str | None:
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value:
                return value
        return None

    def _number_field(
        self,
        payload: Mapping[str, object],
        *names: str,
    ) -> float | None:
        for name in names:
            value = payload.get(name)
            if isinstance(value, int | float):
                return float(value)
        return None

    def _status_field(
        self,
        payload: Mapping[str, object],
        *,
        index: int,
    ) -> BackendVerificationStatus:
        status = self._string_field(payload, "status", "result", "outcome")
        if status is None:
            raise BackendVerificationReportImportError(
                f"backend verification record {index} is missing status",
            )
        normalized = status.lower()
        if normalized in {"ok", "pass", "passed", "success", "successful", "healthy"}:
            return "passed"
        if normalized in {"fail", "failed", "failure", "error", "unhealthy"}:
            return "failed"
        if normalized in {"skip", "skipped"}:
            return "skipped"
        if normalized == "unknown":
            return "unknown"
        raise BackendVerificationReportImportError(
            f"backend verification record {index} has invalid status",
        )

    def _metadata(self, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise BackendVerificationReportImportError(
                "backend verification record metadata must be an object",
            )
        return _json_safe_mapping(value)


@dataclass(frozen=True, slots=True)
class BackendVerificationInvocationPlan:
    """Argv-only invocation metadata for a deployment-owned backend check."""

    command: tuple[str, ...]
    required_backends: tuple[str, ...] = LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command must not be empty")
        _validate_non_empty_names(self.command, field_name="command")
        if not self.required_backends:
            raise ValueError("required_backends must not be empty")
        _validate_non_empty_names(
            self.required_backends,
            field_name="required_backends",
        )
        _reject_restricted_metadata_keys(
            self.metadata,
            allowed_keys=("env_keys",),
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe invocation payload without secret values."""

        return {
            "command": redact_command_argv(self.command),
            "required_backends": self.required_backends,
            "metadata": _json_safe_mapping(self.metadata),
            "sdk_owned": (
                "backend verification invocation plan",
                "argv-only command contract",
                "required backend declaration",
            ),
            "deployment_owned": (
                "backend check script implementation",
                "credentials and secret distribution",
                "network and TLS policy",
                "migration execution",
                "CI matrix execution",
                "alert routing and runbooks",
            ),
        }


@dataclass(frozen=True, slots=True)
class DeploymentLiveBackendVerificationRunResult:
    """JSON-safe execution evidence for an external backend verification run."""

    command: tuple[str, ...]
    exit_code: int
    required_backends: tuple[str, ...] = LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    records: tuple[BackendVerificationRecord, ...] = ()
    started_at: float | None = None
    ended_at: float | None = None
    environment: str | None = None
    artifact_uri: str | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command must not be empty")
        _validate_non_empty_names(self.command, field_name="command")
        if self.exit_code < 0:
            raise ValueError("exit_code must be non-negative")
        if not self.required_backends:
            raise ValueError("required_backends must not be empty")
        _validate_non_empty_names(
            self.required_backends,
            field_name="required_backends",
        )
        _validate_optional_text(self.environment, field_name="environment")
        _validate_optional_text(self.artifact_uri, field_name="artifact_uri")
        if (
            self.started_at is not None
            and self.ended_at is not None
            and self.ended_at < self.started_at
        ):
            raise ValueError("ended_at must be greater than or equal to started_at")
        _reject_restricted_metadata_keys(
            self.metadata,
            allowed_keys=("env_keys",),
        )

    @property
    def execution_succeeded(self) -> bool:
        """Return whether the external backend check process exited cleanly."""

        return self.exit_code == 0

    @property
    def duration_seconds(self) -> float | None:
        """Return run duration when timestamps are available."""

        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    @property
    def accepted(self) -> bool:
        """Return whether execution and backend evidence satisfy readiness."""

        return self.execution_succeeded and self.gate_report().accepted

    @property
    def block_production_readiness(self) -> bool:
        """Return whether this run should block production readiness."""

        return not self.accepted

    def gate_report(self) -> DeploymentLiveBackendVerificationGateReport:
        """Return the evidence coverage gate for this run's backend records."""

        return DeploymentLiveBackendVerificationGateReport.from_records(
            self.records,
            required_backends=self.required_backends,
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe run result payload."""

        gate = self.gate_report()
        payload: dict[str, object] = {
            "command": redact_command_argv(self.command),
            "exit_code": self.exit_code,
            "execution_succeeded": self.execution_succeeded,
            "accepted": self.accepted,
            "block_production_readiness": self.block_production_readiness,
            "required_backends": self.required_backends,
            "records": tuple(record.as_dict() for record in self.records),
            "gate_report": gate.as_dict(),
        }
        if self.started_at is not None:
            payload["started_at"] = self.started_at
        if self.ended_at is not None:
            payload["ended_at"] = self.ended_at
        duration = self.duration_seconds
        if duration is not None:
            payload["duration_seconds"] = duration
        if self.environment is not None:
            payload["environment"] = self.environment
        if self.artifact_uri is not None:
            payload["artifact_uri"] = self.artifact_uri
        if self.stdout_summary:
            payload["stdout_summary"] = self.stdout_summary
        if self.stderr_summary:
            payload["stderr_summary"] = self.stderr_summary
        if self.metadata:
            payload["metadata"] = _json_safe_mapping(self.metadata)
        return payload


class BackendVerificationRunner(Protocol):
    """Protocol for SDK reference adapters that execute backend checks."""

    def run(
        self,
        plan: BackendVerificationInvocationPlan,
    ) -> DeploymentLiveBackendVerificationRunResult:
        """Execute a backend verification invocation plan."""


@dataclass(frozen=True, slots=True)
class BackendVerificationCliRunner:
    """Reference argv-only CLI runner for deployment-owned backend checks."""

    timeout_seconds: float | None = None
    report_path: Path | str | None = None
    environment: str | None = None
    artifact_uri: str | None = None
    stdout_limit: int = 4000
    stderr_limit: int = 4000
    env: Mapping[str, str] | None = None
    report_importer: BackendVerificationReportImporter = field(
        default_factory=BackendVerificationReportImporter,
    )

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.stdout_limit < 0:
            raise ValueError("stdout_limit must be non-negative")
        if self.stderr_limit < 0:
            raise ValueError("stderr_limit must be non-negative")
        _validate_optional_text(self.environment, field_name="environment")
        _validate_optional_text(self.artifact_uri, field_name="artifact_uri")
        if self.env is not None:
            _validate_non_empty_names(
                tuple(str(key) for key in self.env.keys()),
                field_name="env",
            )

    def run(
        self,
        plan: BackendVerificationInvocationPlan,
    ) -> DeploymentLiveBackendVerificationRunResult:
        """Run the plan command and return JSON-safe backend evidence."""

        started_at = time.time()
        try:
            completed = subprocess.run(
                plan.command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=self._subprocess_env(),
            )
        except subprocess.TimeoutExpired as error:
            ended_at = time.time()
            stdout_summary = _bounded_text(
                self._redact_env_values(
                    _text_from_subprocess_output(error.stdout),
                ),
                self.stdout_limit,
            )
            stderr_text = self._redact_env_values(
                _text_from_subprocess_output(error.stderr),
            )
            stderr_summary = _bounded_text(
                (
                    stderr_text
                    + ("\n" if stderr_text else "")
                    + (
                        "backend verification command timed out after "
                        f"{error.timeout} seconds"
                    )
                ),
                self.stderr_limit,
            )
            return self._result(
                plan,
                exit_code=124,
                started_at=started_at,
                ended_at=ended_at,
                stdout_summary=stdout_summary,
                stderr_summary=stderr_summary,
                records=(),
                report_source=None,
                report_import_error=None,
                timed_out=True,
            )
        ended_at = time.time()
        stdout_summary = _bounded_text(
            self._redact_env_values(completed.stdout),
            self.stdout_limit,
        )
        stderr_summary = _bounded_text(
            self._redact_env_values(completed.stderr),
            self.stderr_limit,
        )
        records, report_source, report_error = self._import_report(stdout_summary)
        return self._result(
            plan,
            exit_code=completed.returncode,
            started_at=started_at,
            ended_at=ended_at,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            records=records,
            report_source=report_source,
            report_import_error=report_error,
            timed_out=False,
        )

    def _result(
        self,
        plan: BackendVerificationInvocationPlan,
        *,
        exit_code: int,
        started_at: float,
        ended_at: float,
        stdout_summary: str,
        stderr_summary: str,
        records: tuple[BackendVerificationRecord, ...],
        report_source: str | None,
        report_import_error: str | None,
        timed_out: bool,
    ) -> DeploymentLiveBackendVerificationRunResult:
        return DeploymentLiveBackendVerificationRunResult(
            command=plan.command,
            exit_code=exit_code,
            required_backends=plan.required_backends,
            records=records,
            started_at=started_at,
            ended_at=ended_at,
            environment=self.environment,
            artifact_uri=self.artifact_uri,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            metadata=self._metadata(
                plan=plan,
                report_source=report_source,
                report_import_error=report_import_error,
                timed_out=timed_out,
            ),
        )

    def _subprocess_env(self) -> Mapping[str, str] | None:
        if self.env is None:
            return {}
        return {str(key): str(value) for key, value in self.env.items()}

    def _redact_env_values(self, value: str) -> str:
        if self.env is None:
            return value
        redacted = value
        for secret in self.env.values():
            if secret:
                redacted = redacted.replace(str(secret), "[redacted]")
        return redacted

    def _import_report(
        self,
        stdout_summary: str,
    ) -> tuple[tuple[BackendVerificationRecord, ...], str | None, str | None]:
        if self.report_path is not None:
            path = Path(self.report_path)
            try:
                return (
                    self.report_importer.from_json(
                        path.read_text(encoding="utf-8"),
                    ),
                    str(path),
                    None,
                )
            except (OSError, BackendVerificationReportImportError) as error:
                return (), str(path), str(error)
        if stdout_summary.strip().startswith("{"):
            try:
                return (
                    self.report_importer.from_json(stdout_summary),
                    "stdout",
                    None,
                )
            except BackendVerificationReportImportError as error:
                return (), "stdout", str(error)
        return (), None, None

    def _metadata(
        self,
        *,
        plan: BackendVerificationInvocationPlan,
        report_source: str | None,
        report_import_error: str | None,
        timed_out: bool,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "runner": self.__class__.__name__,
            "timeout_seconds": self.timeout_seconds,
            "timed_out": timed_out,
            "plan_metadata": _json_safe_mapping(plan.metadata),
            "no_backend_client_claim": True,
        }
        if report_source is not None:
            metadata["report_source"] = report_source
        if report_import_error is not None:
            metadata["report_import_error"] = report_import_error
        if self.env is not None:
            metadata["env_keys"] = tuple(sorted(str(key) for key in self.env))
        return metadata


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
    stop_requested_at: float | None = None
    stopped_at: float | None = None
    exit_code: int | None = None
    error: str | None = None

    @classmethod
    def from_spec(
        cls,
        spec: WorkerProcessSpec,
        *,
        status: WorkerProcessStatus = "configured",
        pid: int | None = None,
        started_at: float | None = None,
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
            "stop_requested_at": self.stop_requested_at,
            "stopped_at": self.stopped_at,
            "exit_code": self.exit_code,
            "error": self.error,
        }


class WorkerProcessSupervisor(Protocol):
    """Boundary for local worker process lifecycle evidence."""

    def start(self, spec: WorkerProcessSpec) -> WorkerProcessState:
        """Start one worker process and return its running state."""

    def stop(
        self,
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkerProcessState:
        """Request a worker process stop and return final or current state."""

    def wait(
        self,
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkerProcessState:
        """Wait for a worker process exit and return final or current state."""

    def state(self, worker_id: str) -> WorkerProcessState:
        """Return the latest known state for one worker."""

    def is_running(self, worker_id: str) -> bool:
        """Return whether the worker process is currently alive."""

    def evidence(self, worker_id: str) -> dict[str, object]:
        """Return JSON-safe lifecycle evidence for one worker."""


class LocalSubprocessWorkerSupervisor:
    """Reference subprocess-backed worker supervisor for local deployments."""

    def __init__(self, *, clock: object | None = None) -> None:
        self._clock = clock if callable(clock) else time.time
        self._lock = RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._states: dict[str, WorkerProcessState] = {}

    def start(self, spec: WorkerProcessSpec) -> WorkerProcessState:
        """Start one local subprocess without shell parsing."""

        with self._lock:
            self._reject_duplicate_running_worker(spec.worker_id)
            started_at = float(self._clock())
            try:
                process = subprocess.Popen(
                    spec.command,
                    cwd=spec.cwd,
                    env=self._process_env(spec),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError as exc:
                state = WorkerProcessState.from_spec(
                    spec,
                    status="failed",
                    started_at=started_at,
                    stopped_at=float(self._clock()),
                    error=str(exc) or exc.__class__.__name__,
                )
                self._states[spec.worker_id] = state
                raise
            state = WorkerProcessState.from_spec(
                spec,
                status="running",
                pid=process.pid,
                started_at=started_at,
            )
            self._processes[spec.worker_id] = process
            self._states[spec.worker_id] = state
            return state

    def stop(
        self,
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkerProcessState:
        """Terminate one local subprocess and record stop evidence."""

        with self._lock:
            process = self._processes.get(worker_id)
            state = self._require_state(worker_id)
            if process is None or process.poll() is not None:
                return self._refresh_locked(worker_id)
            now = float(self._clock())
            state = replace(
                state,
                status="stopping",
                stop_requested_at=state.stop_requested_at or now,
            )
            self._states[worker_id] = state
            process.terminate()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            with self._lock:
                return self._refresh_locked(worker_id)
        with self._lock:
            return self._refresh_locked(worker_id)

    def wait(
        self,
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkerProcessState:
        """Wait for one local subprocess to exit."""

        with self._lock:
            process = self._processes.get(worker_id)
            self._require_state(worker_id)
            if process is None or process.poll() is not None:
                return self._refresh_locked(worker_id)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            with self._lock:
                return self._refresh_locked(worker_id)
        with self._lock:
            return self._refresh_locked(worker_id)

    def state(self, worker_id: str) -> WorkerProcessState:
        """Return the latest known state for one worker."""

        with self._lock:
            return self._refresh_locked(worker_id)

    def is_running(self, worker_id: str) -> bool:
        """Return whether the worker process is currently alive."""

        with self._lock:
            process = self._processes.get(worker_id)
            return process is not None and process.poll() is None

    def evidence(self, worker_id: str) -> dict[str, object]:
        """Return JSON-safe lifecycle evidence for one worker."""

        return self.state(worker_id).to_evidence()

    def _reject_duplicate_running_worker(self, worker_id: str) -> None:
        process = self._processes.get(worker_id)
        if process is not None and process.poll() is None:
            raise ValueError("worker is already running")

    def _process_env(self, spec: WorkerProcessSpec) -> dict[str, str] | None:
        if not spec.env:
            return {}
        return {key: str(value) for key, value in spec.env.items()}

    def _refresh_locked(self, worker_id: str) -> WorkerProcessState:
        state = self._require_state(worker_id)
        process = self._processes.get(worker_id)
        if process is None:
            return state
        exit_code = process.poll()
        if exit_code is None:
            return state
        if state.stopped_at is not None and state.exit_code == exit_code:
            return state
        if state.stop_requested_at is not None:
            status: WorkerProcessStatus = "stopped"
        elif exit_code == 0:
            status = "exited"
        else:
            status = "failed"
        updated = replace(
            state,
            status=status,
            stopped_at=state.stopped_at or float(self._clock()),
            exit_code=exit_code,
        )
        self._states[worker_id] = updated
        return updated

    def _require_state(self, worker_id: str) -> WorkerProcessState:
        try:
            return self._states[worker_id]
        except KeyError as exc:
            raise KeyError(f"unknown worker process: {worker_id}") from exc


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


def _bounded_text(value: str, limit: int) -> str:
    if limit == 0:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit]


def _text_from_subprocess_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


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
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return tuple(_json_safe_value(item) for item in value)
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return repr(value)


__all__ = [
    "BackendVerificationCliRunner",
    "BackendVerificationInvocationPlan",
    "BackendVerificationReportImportError",
    "BackendVerificationReportImporter",
    "BackendVerificationRecord",
    "BackendVerificationRunner",
    "BackendVerificationStatus",
    "DeploymentLiveBackendVerificationGateReport",
    "DeploymentLiveBackendVerificationProfile",
    "DeploymentLiveBackendVerificationRunResult",
    "LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS",
    "LocalSubprocessWorkerSupervisor",
    "PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS",
    "ProductionStatePlaneDeploymentProfile",
    "WorkerProcessKind",
    "WorkerProcessSpec",
    "WorkerProcessState",
    "WorkerProcessStatus",
    "WorkerProcessSupervisor",
]
