from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agentos._redaction import redact_command_argv, redact_secret_patterns
from agentos.deployment_constants import (
    _PLACEHOLDER_EVIDENCE_REFS,
    LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS,
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    BackendVerificationStatus,
)
from agentos.deployment_types import (
    BackendVerificationRecord,
    _json_safe_mapping,
    _reject_restricted_metadata_keys,
    _validate_non_empty_names,
    _validate_optional_text,
)


def _is_trusted_passed_backend_verification(
    record: BackendVerificationRecord,
    *,
    expected_backend_kind: str | None = None,
) -> bool:
    if (
        expected_backend_kind is not None
        and record.backend_kind != expected_backend_kind
    ):
        return False
    if record.checked_at <= 0:
        return False
    if record.target_ref is None:
        return False
    if not record.target_ref.strip():
        return False
    evidence_ref = record.evidence_ref.strip().lower()
    if evidence_ref in _PLACEHOLDER_EVIDENCE_REFS:
        return False
    if evidence_ref.startswith("example://"):
        return False
    if evidence_ref.startswith("placeholder://"):
        return False
    return True


@dataclass(frozen=True, slots=True)
class DeploymentLiveBackendVerificationGateReport:
    """Release/readiness gate over deployment-owned backend verification evidence."""

    required_backends: tuple[str, ...]
    records: tuple[BackendVerificationRecord, ...]
    missing_backends: tuple[str, ...]
    failed_backends: tuple[str, ...]
    skipped_backends: tuple[str, ...]
    unknown_backends: tuple[str, ...]
    invalid_backends: tuple[str, ...]
    duplicate_backends: tuple[str, ...]
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
        by_name: dict[str, BackendVerificationRecord] = {}
        counts: dict[str, int] = {}
        for record in records:
            counts[record.backend_name] = counts.get(record.backend_name, 0) + 1
            by_name.setdefault(record.backend_name, record)
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
        invalid = tuple(
            backend
            for backend in required_backends
            if by_name.get(backend) is not None
            and by_name[backend].status == "passed"
            and not _is_trusted_passed_backend_verification(
                by_name[backend],
                expected_backend_kind=(
                    LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS.get(backend)
                ),
            )
        )
        duplicate = tuple(
            backend for backend in required_backends if counts.get(backend, 0) > 1
        )
        accepted = not (missing or failed or skipped or unknown or invalid or duplicate)
        return cls(
            required_backends=tuple(required_backends),
            records=tuple(records),
            missing_backends=missing,
            failed_backends=failed,
            skipped_backends=skipped,
            unknown_backends=unknown,
            invalid_backends=invalid,
            duplicate_backends=duplicate,
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
            "invalid_backends": self.invalid_backends,
            "duplicate_backends": self.duplicate_backends,
            "records": tuple(record.as_dict() for record in self.records),
            "sdk_owned": (
                "BackendVerificationRecord",
                "DeploymentLiveBackendVerificationGateReport",
                "required backend evidence coverage checks",
                "duplicate backend evidence checks",
                "passed backend evidence trust checks",
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
            payload["stdout_summary"] = redact_secret_patterns(self.stdout_summary)
        if self.stderr_summary:
            payload["stderr_summary"] = redact_secret_patterns(self.stderr_summary)
        if self.metadata:
            payload["metadata"] = _json_safe_mapping(self.metadata)
        return payload


__all__ = [
    "BackendVerificationReportImportError",
    "BackendVerificationReportImporter",
    "DeploymentLiveBackendVerificationGateReport",
    "DeploymentLiveBackendVerificationRunResult",
]
