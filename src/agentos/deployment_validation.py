from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agentos._redaction import redact_command_argv, redact_secret_patterns
from agentos.deployment_constants import (
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
)
from agentos.deployment_reports import (
    BackendVerificationReportImportError,
    BackendVerificationReportImporter,
    DeploymentLiveBackendVerificationRunResult,
)
from agentos.deployment_types import (
    BackendVerificationRecord,
    _json_safe_mapping,
    _reject_restricted_metadata_keys,
    _validate_non_empty_names,
    _validate_optional_text,
)


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
        redacted = redact_secret_patterns(value)
        if self.env is None:
            return redacted
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


__all__ = [
    "BackendVerificationCliRunner",
    "BackendVerificationInvocationPlan",
    "BackendVerificationRunner",
]
