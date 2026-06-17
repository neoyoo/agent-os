from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agentos._redaction import redact_command_argv
from agentos.channels.a2a import (
    A2AAgentCard,
    AllowAllA2AInboundAuthPolicy,
    a2a_card_to_dict,
)
from agentos.channels.a2a_operations import (
    A2AArtifact,
    A2AExtensionNegotiationError,
    A2AExtensionNegotiationPolicy,
    A2AMessage,
    A2AMessagePart,
    A2AOperationRequest,
    A2AOperationServer,
    A2AProtocolVersionError,
    A2AProtocolVersionPolicy,
    A2ATask,
    A2ATaskArtifactUpdateEvent,
    A2ATaskSubscriptionEvent,
    a2a_artifact_to_dict,
    a2a_operation_request_to_dict,
    a2a_task_artifact_update_event_to_dict,
    a2a_task_subscription_event_from_dict,
    a2a_task_subscription_event_to_dict,
    parse_a2a_sse_events,
)


@dataclass(frozen=True, slots=True)
class A2AConformanceCheck:
    """Metadata for one SDK-owned A2A self-conformance check."""

    check_id: str
    title: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class A2AConformanceFinding:
    """Result for one SDK-owned A2A self-conformance check."""

    check_id: str
    title: str
    passed: bool
    detail: str = ""
    evidence: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe finding payload."""

        payload: dict[str, object] = {
            "checkId": self.check_id,
            "title": self.title,
            "passed": self.passed,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.evidence:
            payload["evidence"] = dict(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class A2AConformanceReport:
    """Structured report for an A2A self-conformance run."""

    findings: tuple[A2AConformanceFinding, ...]
    suite: str = "agentos-a2a-self-conformance"
    source: str = "sdk-self"
    version: str | None = None
    run_id: str | None = None
    target: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return whether every check passed."""

        return all(finding.passed for finding in self.findings)

    @property
    def failed_checks(self) -> tuple[A2AConformanceFinding, ...]:
        """Return failed checks in report order."""

        return tuple(finding for finding in self.findings if not finding.passed)

    @property
    def check_ids(self) -> tuple[str, ...]:
        """Return check ids in report order."""

        return tuple(finding.check_id for finding in self.findings)

    def finding(self, check_id: str) -> A2AConformanceFinding:
        """Return one finding by check id."""

        for finding in self.findings:
            if finding.check_id == check_id:
                return finding
        raise KeyError(check_id)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe report payload."""

        payload: dict[str, object] = {
            "suite": self.suite,
            "source": self.source,
            "passed": self.passed,
            "checks": [finding.to_dict() for finding in self.findings],
        }
        if self.version is not None:
            payload["version"] = self.version
        if self.run_id is not None:
            payload["runId"] = self.run_id
        if self.target is not None:
            payload["target"] = self.target
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


class A2AExternalConformanceImportError(ValueError):
    """Raised when an external A2A conformance report cannot be imported."""


class A2AExternalConformanceReportImporter:
    """Import external A2A conformance suite results into SDK report shape."""

    def from_json(self, payload: str) -> A2AConformanceReport:
        """Parse a JSON external report payload."""

        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise A2AExternalConformanceImportError(
                f"invalid JSON external conformance report: {error.msg}",
            ) from error
        if not isinstance(loaded, Mapping):
            raise A2AExternalConformanceImportError(
                "external conformance report must be a JSON object",
            )
        return self.from_mapping(loaded)

    def from_mapping(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceReport:
        """Normalize a mapping payload into an A2AConformanceReport."""

        checks = payload.get("checks")
        if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
            raise A2AExternalConformanceImportError(
                "external conformance report checks must be a list",
            )
        findings = tuple(
            self._finding_from_mapping(item, index=index)
            for index, item in enumerate(checks)
        )
        if not findings:
            raise A2AExternalConformanceImportError(
                "external conformance report checks must not be empty",
            )
        return A2AConformanceReport(
            findings=findings,
            suite=self._string_field(payload, "suite", "name")
            or "external-a2a-conformance",
            source=self._string_field(payload, "source") or "external",
            version=self._string_field(payload, "version"),
            run_id=self._string_field(payload, "runId", "run_id", "runID"),
            target=self._string_field(payload, "target", "url", "endpoint"),
            metadata=self._metadata(payload.get("metadata")),
        )

    def _finding_from_mapping(
        self,
        value: object,
        *,
        index: int,
    ) -> A2AConformanceFinding:
        if not isinstance(value, Mapping):
            raise A2AExternalConformanceImportError(
                f"external conformance check {index} must be an object",
            )
        check_id = self._string_field(value, "checkId", "check_id", "id")
        if check_id is None:
            raise A2AExternalConformanceImportError(
                f"external conformance check {index} is missing check id",
            )
        passed = self._passed_value(value, index=index)
        title = (
            self._string_field(value, "title", "name")
            or check_id
        )
        detail = (
            self._string_field(value, "detail", "error", "message")
            or ""
        )
        return A2AConformanceFinding(
            check_id=check_id,
            title=title,
            passed=passed,
            detail=detail,
            evidence=self._metadata(value.get("evidence")),
        )

    def _passed_value(self, value: Mapping[str, object], *, index: int) -> bool:
        if isinstance(value.get("passed"), bool):
            return bool(value["passed"])
        if isinstance(value.get("success"), bool):
            return bool(value["success"])
        status = self._string_field(value, "status", "result", "outcome")
        if status is not None:
            normalized = status.lower()
            if normalized in {"pass", "passed", "success", "successful", "ok"}:
                return True
            if normalized in {"fail", "failed", "failure", "error", "not_ok"}:
                return False
        raise A2AExternalConformanceImportError(
            f"external conformance check {index} is missing passed result",
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

    def _metadata(self, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise A2AExternalConformanceImportError(
                "external conformance metadata must be an object",
            )
        return {str(key): item for key, item in value.items()}


def _validate_non_empty_names(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain empty names")


def _validate_required_text(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


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


A2A_EXTERNAL_CONFORMANCE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "external_suite_runner",
    "target_endpoint",
    "credential_policy",
    "network_egress_policy",
    "version_matrix",
    "ci_artifact_retention",
    "failure_alerting",
)

A2A_EXTERNAL_CONFORMANCE_REQUIRED_CHECK_IDS: tuple[str, ...] = (
    "agent-card",
    "message-send",
    "message-stream",
    "tasks-resubscribe",
    "push-notification-config",
)


@dataclass(frozen=True, slots=True)
class A2AExternalConformanceInvocationPlan:
    """Preflight metadata for a deployment-owned external A2A suite run."""

    suite_id: str
    target: str
    command: tuple[str, ...]
    suite_version: str | None = None
    credential_policy_ref: str | None = None
    network_egress_policy_ref: str | None = None
    version_matrix_ref: str | None = None
    artifact_retention_ref: str | None = None
    failure_alerting_ref: str | None = None
    required_check_ids: tuple[str, ...] = (
        A2A_EXTERNAL_CONFORMANCE_REQUIRED_CHECK_IDS
    )
    required_components: tuple[str, ...] = (
        A2A_EXTERNAL_CONFORMANCE_REQUIRED_COMPONENTS
    )
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_required_text(self.suite_id, field_name="suite_id")
        _validate_required_text(self.target, field_name="target")
        if not self.command:
            raise ValueError("command must not be empty")
        _validate_non_empty_names(self.command, field_name="command")
        if self.suite_version is not None:
            _validate_optional_text(
                self.suite_version,
                field_name="suite_version",
            )
        _validate_optional_text(
            self.credential_policy_ref,
            field_name="credential_policy_ref",
        )
        _validate_optional_text(
            self.network_egress_policy_ref,
            field_name="network_egress_policy_ref",
        )
        _validate_optional_text(
            self.version_matrix_ref,
            field_name="version_matrix_ref",
        )
        _validate_optional_text(
            self.artifact_retention_ref,
            field_name="artifact_retention_ref",
        )
        _validate_optional_text(
            self.failure_alerting_ref,
            field_name="failure_alerting_ref",
        )
        _validate_non_empty_names(
            self.required_check_ids,
            field_name="required_check_ids",
        )
        _validate_non_empty_names(
            self.required_components,
            field_name="required_components",
        )

    def configured_component_names(self) -> tuple[str, ...]:
        """Return required preflight components configured by this plan."""

        configured = ["external_suite_runner", "target_endpoint"]
        if self.credential_policy_ref is not None:
            configured.append("credential_policy")
        if self.network_egress_policy_ref is not None:
            configured.append("network_egress_policy")
        if self.version_matrix_ref is not None:
            configured.append("version_matrix")
        if self.artifact_retention_ref is not None:
            configured.append("ci_artifact_retention")
        if self.failure_alerting_ref is not None:
            configured.append("failure_alerting")
        return tuple(
            component
            for component in self.required_components
            if component in set(configured)
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required invocation components not configured."""

        configured = set(self.configured_component_names())
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    @property
    def ready(self) -> bool:
        """Return whether the invocation plan has required preflight metadata."""

        return not self.missing_components()

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe invocation plan payload."""

        payload: dict[str, object] = {
            "suite_id": self.suite_id,
            "target": self.target,
            "command": redact_command_argv(self.command),
            "required_check_ids": self.required_check_ids,
            "required_components": self.required_components,
            "configured_components": self.configured_component_names(),
            "missing_components": self.missing_components(),
            "ready": self.ready,
            "no_certification_claim": True,
        }
        if self.suite_version is not None:
            payload["suite_version"] = self.suite_version
        if self.credential_policy_ref is not None:
            payload["credential_policy_ref"] = self.credential_policy_ref
        if self.network_egress_policy_ref is not None:
            payload["network_egress_policy_ref"] = (
                self.network_egress_policy_ref
            )
        if self.version_matrix_ref is not None:
            payload["version_matrix_ref"] = self.version_matrix_ref
        if self.artifact_retention_ref is not None:
            payload["artifact_retention_ref"] = self.artifact_retention_ref
        if self.failure_alerting_ref is not None:
            payload["failure_alerting_ref"] = self.failure_alerting_ref
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe external suite invocation readiness metadata."""

        gate = A2AExternalConformanceInvocationGateReport.from_plan(self)
        return gate.as_dict()

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def to_execution_record(
        self,
        *,
        exit_code: int,
        started_at: float | None = None,
        ended_at: float | None = None,
        environment: str | None = None,
        artifact_uri: str | None = None,
        stdout_summary: str = "",
        stderr_summary: str = "",
        report: A2AConformanceReport | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> A2AExternalConformanceExecutionRecord:
        """Project an already-executed suite run into an execution record."""

        record_metadata: dict[str, object] = {
            "suite_version": self.suite_version,
            "plan_metadata": dict(self.metadata),
        }
        if metadata:
            record_metadata.update(dict(metadata))
        return A2AExternalConformanceExecutionRecord(
            suite=self.suite_id,
            target=self.target,
            command=self.command,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            environment=environment,
            artifact_uri=artifact_uri,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            report=report,
            metadata=record_metadata,
        )


@dataclass(frozen=True, slots=True)
class A2AExternalConformanceInvocationGateReport:
    """Preflight gate over an external A2A suite invocation plan."""

    plan: A2AExternalConformanceInvocationPlan
    required_components: tuple[str, ...] = (
        A2A_EXTERNAL_CONFORMANCE_REQUIRED_COMPONENTS
    )
    configured_components: tuple[str, ...] = ()
    missing_components: tuple[str, ...] = ()

    @classmethod
    def from_plan(
        cls,
        plan: A2AExternalConformanceInvocationPlan,
    ) -> A2AExternalConformanceInvocationGateReport:
        """Build a preflight gate report from one invocation plan."""

        return cls(
            plan=plan,
            required_components=plan.required_components,
            configured_components=plan.configured_component_names(),
            missing_components=plan.missing_components(),
        )

    @property
    def ready(self) -> bool:
        """Return whether the invocation plan satisfies preflight gating."""

        return not self.missing_components

    @property
    def status(self) -> str:
        """Return readiness status text."""

        return "ok" if self.ready else "failed"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe invocation gate payload."""

        return {
            "profile": self.__class__.__name__,
            "ready": self.ready,
            "ok": self.ready,
            "status": self.status,
            "plan": self.plan.as_dict(),
            "suite_id": self.plan.suite_id,
            "suite_version": self.plan.suite_version,
            "target": self.plan.target,
            "command": redact_command_argv(self.plan.command),
            "required_check_ids": self.plan.required_check_ids,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": self.missing_components,
            "no_certification_claim": True,
            "sdk_owned": (
                "external conformance invocation plan",
                "external conformance invocation gate report",
                "required-check declaration",
                "preflight component gating",
                "execution record projection",
                "JSON-safe readiness metadata",
                "no certification claim",
            ),
            "deployment_owned": (
                "external suite selection",
                "external suite execution",
                "CI pipeline wiring",
                "target environment provisioning",
                "credential issuance and secret distribution",
                "network egress policy enforcement",
                "DNS pinning and CA trust rollout",
                "version matrix execution",
                "artifact upload and retention",
                "failure alerting and release gating policy",
                "certification program or vendor attestation",
            ),
        }


@dataclass(frozen=True, slots=True)
class A2AExternalConformanceExecutionRecord:
    """JSON-safe metadata for one externally executed A2A suite attempt."""

    suite: str
    target: str
    command: tuple[str, ...]
    exit_code: int
    started_at: float | None = None
    ended_at: float | None = None
    environment: str | None = None
    artifact_uri: str | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    report: A2AConformanceReport | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.suite.strip():
            raise ValueError("suite must not be empty")
        if not self.target.strip():
            raise ValueError("target must not be empty")
        if not self.command:
            raise ValueError("command must not be empty")
        if any(not item.strip() for item in self.command):
            raise ValueError("command must not contain empty values")
        if self.exit_code < 0:
            raise ValueError("exit_code must be non-negative")
        if (
            self.started_at is not None
            and self.ended_at is not None
            and self.ended_at < self.started_at
        ):
            raise ValueError("ended_at must be greater than or equal to started_at")

    @property
    def execution_succeeded(self) -> bool:
        """Return whether the external suite process reported success."""

        return self.exit_code == 0

    @property
    def duration_seconds(self) -> float | None:
        """Return execution duration when start/end timestamps are available."""

        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe execution record payload."""

        payload: dict[str, object] = {
            "suite": self.suite,
            "target": self.target,
            "command": redact_command_argv(self.command),
            "exit_code": self.exit_code,
            "execution_succeeded": self.execution_succeeded,
            "report_present": self.report is not None,
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
            payload["metadata"] = dict(self.metadata)
        if self.report is not None:
            payload.update(
                {
                    "report_suite": self.report.suite,
                    "report_source": self.report.source,
                    "report_version": self.report.version,
                    "report_run_id": self.report.run_id,
                    "report_target": self.report.target,
                    "report_passed": self.report.passed,
                    "report_check_ids": self.report.check_ids,
                },
            )
        return payload


@dataclass(frozen=True, slots=True)
class A2AExternalConformanceGateReport:
    """Release/readiness gate over one external conformance execution record."""

    record: A2AExternalConformanceExecutionRecord
    required_check_ids: tuple[str, ...] = (
        A2A_EXTERNAL_CONFORMANCE_REQUIRED_CHECK_IDS
    )
    required_components: tuple[str, ...] = (
        A2A_EXTERNAL_CONFORMANCE_REQUIRED_COMPONENTS
    )
    configured_components: tuple[str, ...] = ()
    missing_required_checks: tuple[str, ...] = ()
    failed_required_checks: tuple[str, ...] = ()
    missing_components: tuple[str, ...] = ()

    @classmethod
    def from_record(
        cls,
        record: A2AExternalConformanceExecutionRecord,
        *,
        required_check_ids: tuple[str, ...] = (
            A2A_EXTERNAL_CONFORMANCE_REQUIRED_CHECK_IDS
        ),
        required_components: tuple[str, ...] = (
            A2A_EXTERNAL_CONFORMANCE_REQUIRED_COMPONENTS
        ),
        configured_components: tuple[str, ...] = (),
    ) -> A2AExternalConformanceGateReport:
        """Build a gate report from one execution record."""

        _validate_non_empty_names(
            required_check_ids,
            field_name="required_check_ids",
        )
        _validate_non_empty_names(
            required_components,
            field_name="required_components",
        )
        _validate_non_empty_names(
            configured_components,
            field_name="configured_components",
        )
        check_ids = set(record.report.check_ids) if record.report else set()
        required = set(required_check_ids)
        failed = ()
        if record.report is not None:
            failed = tuple(
                finding.check_id
                for finding in record.report.findings
                if finding.check_id in required and not finding.passed
            )
        return cls(
            record=record,
            required_check_ids=required_check_ids,
            required_components=required_components,
            configured_components=configured_components,
            missing_required_checks=tuple(
                check_id
                for check_id in required_check_ids
                if check_id not in check_ids
            ),
            failed_required_checks=failed,
            missing_components=tuple(
                component
                for component in required_components
                if component not in set(configured_components)
            ),
        )

    @property
    def execution_succeeded(self) -> bool:
        """Return whether the external suite process reported success."""

        return self.record.execution_succeeded

    @property
    def report_present(self) -> bool:
        """Return whether a normalized conformance report is attached."""

        return self.record.report is not None

    @property
    def ready(self) -> bool:
        """Return whether the execution record satisfies the local gate."""

        return (
            self.execution_succeeded
            and self.report_present
            and not self.missing_required_checks
            and not self.failed_required_checks
            and not self.missing_components
        )

    @property
    def status(self) -> str:
        """Return readiness status text."""

        return "ok" if self.ready else "failed"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe release/readiness gate payload."""

        report = self.record.report
        payload: dict[str, object] = {
            "profile": self.__class__.__name__,
            "ready": self.ready,
            "ok": self.ready,
            "status": self.status,
            "record": self.record.as_dict(),
            "execution_succeeded": self.execution_succeeded,
            "report_present": self.report_present,
            "report_passed": report.passed if report is not None else False,
            "required_check_ids": self.required_check_ids,
            "missing_required_checks": self.missing_required_checks,
            "failed_required_checks": self.failed_required_checks,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": self.missing_components,
            "no_certification_claim": True,
            "sdk_owned": (
                "external conformance execution record",
                "external conformance gate report",
                "external report import and normalization",
                "required-check gating",
                "failed-check projection",
                "JSON-safe release gate metadata",
                "no certification claim",
            ),
            "deployment_owned": (
                "external suite execution",
                "CI pipeline wiring",
                "target environment provisioning",
                "credential issuance and secret distribution",
                "network egress policy",
                "DNS pinning and CA trust rollout",
                "version matrix selection",
                "artifact upload and retention",
                "failure alerting and release gating policy",
                "certification program or vendor attestation",
            ),
        }
        if report is not None:
            payload.update(
                {
                    "suite": report.suite,
                    "source": report.source,
                    "version": report.version,
                    "run_id": report.run_id,
                    "target": report.target,
                    "check_ids": report.check_ids,
                },
            )
        return payload


class A2AExternalConformanceRunner(Protocol):
    """Protocol for SDK reference adapters that execute external A2A suites."""

    def run(
        self,
        plan: A2AExternalConformanceInvocationPlan,
    ) -> A2AExternalConformanceExecutionRecord:
        """Execute an external conformance invocation plan."""


@dataclass(frozen=True, slots=True)
class A2AExternalConformanceCliRunner:
    """Reference argv-only CLI runner for external A2A conformance suites."""

    timeout_seconds: float | None = None
    report_path: Path | str | None = None
    environment: str | None = None
    artifact_uri: str | None = None
    stdout_limit: int = 4000
    stderr_limit: int = 4000
    env: Mapping[str, str] | None = None
    report_importer: A2AExternalConformanceReportImporter = field(
        default_factory=A2AExternalConformanceReportImporter,
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
        plan: A2AExternalConformanceInvocationPlan,
    ) -> A2AExternalConformanceExecutionRecord:
        """Run the plan command and return JSON-safe execution evidence."""

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
                    + (
                        "\n"
                        if stderr_text
                        else ""
                    )
                    + f"external conformance command timed out after "
                    f"{error.timeout} seconds"
                ),
                self.stderr_limit,
            )
            return plan.to_execution_record(
                exit_code=124,
                started_at=started_at,
                ended_at=ended_at,
                environment=self.environment,
                artifact_uri=self.artifact_uri,
                stdout_summary=stdout_summary,
                stderr_summary=stderr_summary,
                report=None,
                metadata=self._metadata(
                    report_source=None,
                    report_import_error=None,
                    timed_out=True,
                ),
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
        report, report_source, report_error = self._import_report(
            stdout_summary,
        )
        metadata = self._metadata(
            report_source=report_source,
            report_import_error=report_error,
            timed_out=False,
        )
        return plan.to_execution_record(
            exit_code=completed.returncode,
            started_at=started_at,
            ended_at=ended_at,
            environment=self.environment,
            artifact_uri=self.artifact_uri,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            report=report,
            metadata=metadata,
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
                redacted = redacted.replace(secret, "[redacted]")
        return redacted

    def _import_report(
        self,
        stdout_summary: str,
    ) -> tuple[A2AConformanceReport | None, str | None, str | None]:
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
            except (OSError, A2AExternalConformanceImportError) as error:
                return None, str(path), str(error)
        if stdout_summary.strip().startswith("{"):
            try:
                return (
                    self.report_importer.from_json(stdout_summary),
                    "stdout",
                    None,
                )
            except A2AExternalConformanceImportError as error:
                return None, "stdout", str(error)
        return None, None, None

    def _metadata(
        self,
        *,
        report_source: str | None,
        report_import_error: str | None,
        timed_out: bool,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "runner": self.__class__.__name__,
            "timeout_seconds": self.timeout_seconds,
            "timed_out": timed_out,
            "no_certification_claim": True,
        }
        if report_source is not None:
            metadata["report_source"] = report_source
        if report_import_error is not None:
            metadata["report_import_error"] = report_import_error
        if self.env is not None:
            metadata["env_keys"] = tuple(sorted(str(key) for key in self.env))
        return metadata


@dataclass(frozen=True, slots=True)
class A2AExternalConformanceExecutionProfile:
    """Deployment-facing readiness for externally executed A2A conformance."""

    report: A2AConformanceReport | None = None
    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        A2A_EXTERNAL_CONFORMANCE_REQUIRED_COMPONENTS
    )
    required_check_ids: tuple[str, ...] = (
        A2A_EXTERNAL_CONFORMANCE_REQUIRED_CHECK_IDS
    )
    probe_name: str = "a2a_external_conformance_execution"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        self._validate_non_empty(
            self.required_components,
            field_name="required_components",
        )
        self._validate_non_empty(
            self.configured_components,
            field_name="configured_components",
        )
        self._validate_non_empty(
            self.required_check_ids,
            field_name="required_check_ids",
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required execution components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def missing_required_checks(self) -> tuple[str, ...]:
        """Return required external checks not present in the report."""

        if self.report is None:
            return self.required_check_ids
        present = set(self.report.check_ids)
        return tuple(
            check_id
            for check_id in self.required_check_ids
            if check_id not in present
        )

    def failed_required_checks(self) -> tuple[str, ...]:
        """Return required external checks present but failed."""

        if self.report is None:
            return ()
        required = set(self.required_check_ids)
        return tuple(
            finding.check_id
            for finding in self.report.findings
            if finding.check_id in required and not finding.passed
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe external conformance execution readiness metadata."""

        missing_components = self.missing_components()
        missing_checks = self.missing_required_checks()
        failed_checks = self.failed_required_checks()
        ready = (
            self.report is not None
            and not missing_components
            and not missing_checks
            and not failed_checks
        )
        metadata: dict[str, object] = {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": ready,
            "report_present": self.report is not None,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing_components,
            "required_check_ids": self.required_check_ids,
            "missing_required_checks": missing_checks,
            "failed_required_checks": failed_checks,
            "sdk_owned": (
                "external report import and normalization",
                "required-check gating",
                "failed-check projection",
                "JSON-safe readiness metadata",
                "no certification claim",
            ),
            "deployment_owned": (
                "external suite execution",
                "target environment provisioning",
                "credential issuance and secret distribution",
                "network egress policy",
                "CA trust rollout",
                "version matrix selection",
                "CI artifact retention",
                "failure alerting and release gating",
                "certification program or vendor attestation",
            ),
        }
        if self.report is not None:
            metadata.update(
                {
                    "suite": self.report.suite,
                    "source": self.report.source,
                    "version": self.report.version,
                    "run_id": self.report.run_id,
                    "target": self.report.target,
                    "report_passed": self.report.passed,
                    "check_ids": self.report.check_ids,
                },
            )
        return metadata

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _validate_non_empty(
        self,
        values: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not value.strip() for value in values):
            raise ValueError(f"{field_name} must not contain empty names")


class A2AConformanceHarness:
    """Run SDK-owned A2A protocol self-conformance checks."""

    def run(
        self,
        card: A2AAgentCard,
        *,
        message_payload: Mapping[str, object] | None = None,
        artifact_payload: Mapping[str, object] | None = None,
        status_event_payload: Mapping[str, object] | None = None,
        artifact_event_payload: Mapping[str, object] | None = None,
        operation_request_payload: Mapping[str, object] | None = None,
        stream_operation_request_payload: Mapping[str, object] | None = None,
        stream_event_payload: Mapping[str, object] | None = None,
        task_resubscribe_request_payload: Mapping[str, object] | None = None,
        task_resubscribe_event_payload: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        protocol_version_policy: A2AProtocolVersionPolicy | None = None,
        extension_negotiation_policy: (
            A2AExtensionNegotiationPolicy | None
        ) = None,
    ) -> A2AConformanceReport:
        """Run local A2A self-checks and return a structured report."""

        samples = _A2ASamplePayloads.build(
            message_payload=message_payload,
            artifact_payload=artifact_payload,
            status_event_payload=status_event_payload,
            artifact_event_payload=artifact_event_payload,
            operation_request_payload=operation_request_payload,
            stream_operation_request_payload=stream_operation_request_payload,
            stream_event_payload=stream_event_payload,
            task_resubscribe_request_payload=task_resubscribe_request_payload,
            task_resubscribe_event_payload=task_resubscribe_event_payload,
        )
        version_policy = protocol_version_policy or A2AProtocolVersionPolicy()
        extension_policy = (
            extension_negotiation_policy or A2AExtensionNegotiationPolicy()
        )
        findings = (
            self._check_agent_card_required_fields(card),
            self._check_agent_card_extension_shape(card),
            self._check_message_part_wrapper_shape(samples.message_payload),
            self._check_artifact_wrapper_shape(samples.artifact_payload),
            self._check_status_event_wrapper_shape(samples.status_event_payload),
            self._check_artifact_event_wrapper_shape(samples.artifact_event_payload),
            self._check_operation_jsonrpc_envelope(
                samples.operation_request_payload,
            ),
            self._check_message_stream_jsonrpc_envelope(
                samples.stream_operation_request_payload,
            ),
            self._check_message_stream_event_shape(samples.stream_event_payload),
            self._check_task_resubscribe_jsonrpc_envelope(
                samples.task_resubscribe_request_payload,
            ),
            self._check_task_resubscribe_event_shape(
                samples.task_resubscribe_event_payload,
            ),
            self._check_protocol_version(version_policy, headers),
            self._check_extension_negotiation(extension_policy, card, headers),
            self._check_extension_required_error_mapping(
                extension_policy,
                samples.operation_request_payload,
                headers,
                version_policy,
            ),
        )
        return A2AConformanceReport(findings=findings)

    def _check_agent_card_required_fields(
        self,
        card: A2AAgentCard,
    ) -> A2AConformanceFinding:
        try:
            payload = a2a_card_to_dict(card)
        except Exception as error:
            return _failed(
                "agent-card-required-fields",
                "Agent Card required fields",
                str(error),
            )
        missing: list[str] = []
        for name in ("protocolVersion", "name", "description", "url", "version"):
            value = payload.get(name)
            if not isinstance(value, str) or not value:
                missing.append(name)
        if not isinstance(payload.get("capabilities"), Mapping):
            missing.append("capabilities")
        for name in ("defaultInputModes", "defaultOutputModes", "skills"):
            if not isinstance(payload.get(name), list):
                missing.append(name)
        if missing:
            return _failed(
                "agent-card-required-fields",
                "Agent Card required fields",
                "Agent Card is missing required public fields.",
                {"missingFields": missing},
            )
        return _passed(
            "agent-card-required-fields",
            "Agent Card required fields",
            {"fields": tuple(payload.keys())},
        )

    def _check_agent_card_extension_shape(
        self,
        card: A2AAgentCard,
    ) -> A2AConformanceFinding:
        payload = a2a_card_to_dict(card)
        capabilities = payload.get("capabilities")
        extensions: object = None
        if isinstance(capabilities, Mapping):
            extensions = capabilities.get("extensions")
        if not card.capabilities.extensions:
            return _passed(
                "agent-card-extension-shape",
                "Agent Card extension shape",
                {"extensions": []},
            )
        if not isinstance(extensions, list) or not extensions:
            return _failed(
                "agent-card-extension-shape",
                "Agent Card extension shape",
                "Declared card extensions did not serialize under capabilities.extensions.",
            )
        invalid = [
            index
            for index, extension in enumerate(extensions)
            if not isinstance(extension, Mapping)
            or not isinstance(extension.get("uri"), str)
            or not extension.get("uri")
        ]
        if invalid:
            return _failed(
                "agent-card-extension-shape",
                "Agent Card extension shape",
                "One or more serialized extensions are missing uri.",
                {"invalidIndexes": invalid},
            )
        return _passed(
            "agent-card-extension-shape",
            "Agent Card extension shape",
            {"extensionUris": [extension["uri"] for extension in extensions]},
        )

    def _check_message_part_wrapper_shape(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        parts = payload.get("parts")
        failures = _part_wrapper_failures(parts)
        if failures:
            return _failed(
                "message-part-wrapper-shape",
                "Message part wrapper shape",
                "Message parts must use A2A 1.0 wrapper objects without kind.",
                {"failures": failures},
            )
        return _passed(
            "message-part-wrapper-shape",
            "Message part wrapper shape",
            {"partCount": len(parts) if isinstance(parts, Sequence) else 0},
        )

    def _check_artifact_wrapper_shape(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        failures: list[str] = []
        artifact_id = payload.get("artifactId")
        if not isinstance(artifact_id, str) or not artifact_id:
            failures.append("artifactId is required")
        failures.extend(_part_wrapper_failures(payload.get("parts")))
        if failures:
            return _failed(
                "artifact-wrapper-shape",
                "Artifact wrapper shape",
                "Artifacts must use artifactId and A2A 1.0 part wrappers.",
                {"failures": failures},
            )
        return _passed(
            "artifact-wrapper-shape",
            "Artifact wrapper shape",
            {"artifactId": artifact_id},
        )

    def _check_status_event_wrapper_shape(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        status_update = payload.get("statusUpdate")
        if not isinstance(status_update, Mapping):
            return _failed(
                "status-event-wrapper-shape",
                "Status event wrapper shape",
                "Task status events must use statusUpdate.",
            )
        return _passed(
            "status-event-wrapper-shape",
            "Status event wrapper shape",
            {"taskId": status_update.get("taskId")},
        )

    def _check_artifact_event_wrapper_shape(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        artifact_update = payload.get("artifactUpdate")
        if not isinstance(artifact_update, Mapping):
            return _failed(
                "artifact-event-wrapper-shape",
                "Artifact event wrapper shape",
                "Task artifact events must use artifactUpdate.",
            )
        artifact = artifact_update.get("artifact")
        if not isinstance(artifact, Mapping) or not artifact.get("artifactId"):
            return _failed(
                "artifact-event-wrapper-shape",
                "Artifact event wrapper shape",
                "Artifact update payload must include artifact.artifactId.",
            )
        return _passed(
            "artifact-event-wrapper-shape",
            "Artifact event wrapper shape",
            {"taskId": artifact_update.get("taskId")},
        )

    def _check_operation_jsonrpc_envelope(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        failures: list[str] = []
        if payload.get("jsonrpc") != "2.0":
            failures.append("jsonrpc must be 2.0")
        if not isinstance(payload.get("method"), str) or not payload.get("method"):
            failures.append("method is required")
        if not isinstance(payload.get("params"), Mapping):
            failures.append("params must be an object")
        if failures:
            return _failed(
                "operation-jsonrpc-envelope",
                "Operation JSON-RPC envelope",
                "Operation requests must use the JSON-RPC envelope.",
                {"failures": failures},
            )
        return _passed(
            "operation-jsonrpc-envelope",
            "Operation JSON-RPC envelope",
            {"method": payload.get("method"), "hasId": "id" in payload},
        )

    def _check_message_stream_jsonrpc_envelope(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        failures: list[str] = []
        if payload.get("jsonrpc") != "2.0":
            failures.append("jsonrpc must be 2.0")
        if payload.get("method") != "SendStreamingMessage":
            failures.append("method must be SendStreamingMessage")
        params = payload.get("params")
        if not isinstance(params, Mapping):
            failures.append("params must be an object")
        elif not isinstance(params.get("message"), Mapping):
            failures.append("params.message must be an object")
        if failures:
            return _failed(
                "message-stream-jsonrpc-envelope",
                "Message stream JSON-RPC envelope",
                "SendStreamingMessage requests must use the JSON-RPC envelope.",
                {"failures": failures, "method": payload.get("method")},
            )
        return _passed(
            "message-stream-jsonrpc-envelope",
            "Message stream JSON-RPC envelope",
            {"method": payload.get("method"), "hasId": "id" in payload},
        )

    def _check_message_stream_event_shape(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        try:
            events = parse_a2a_sse_events((_sse_event_chunk(payload),))
        except Exception as error:
            return _failed(
                "message-stream-event-shape",
                "Message stream event shape",
                f"message stream event payload must parse as a typed SDK event: {error}",
            )
        if not events:
            return _failed(
                "message-stream-event-shape",
                "Message stream event shape",
                "message stream event payload must produce at least one event.",
            )
        event = events[0]
        event_type = "unknown"
        if event.task is not None:
            event_type = "task"
        elif event.message is not None:
            event_type = "message"
        elif event.task_event is not None:
            event_type = "statusUpdate"
        elif event.artifact_event is not None:
            event_type = "artifactUpdate"
        return _passed(
            "message-stream-event-shape",
            "Message stream event shape",
            {"eventType": event_type, "eventName": event.event},
        )

    def _check_task_resubscribe_jsonrpc_envelope(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        failures: list[str] = []
        if payload.get("jsonrpc") != "2.0":
            failures.append("jsonrpc must be 2.0")
        if payload.get("method") != "SubscribeToTask":
            failures.append("method must be SubscribeToTask")
        params = payload.get("params")
        if not isinstance(params, Mapping):
            failures.append("params must be an object")
        else:
            task_id = params.get("id")
            if not isinstance(task_id, str) or not task_id:
                failures.append("params.id must be a non-empty string")
            after_event_id = params.get("afterEventId")
            if after_event_id is not None and not isinstance(after_event_id, int):
                failures.append("params.afterEventId must be an integer")
        if failures:
            return _failed(
                "task-resubscribe-jsonrpc-envelope",
                "Task resubscribe JSON-RPC envelope",
                "SubscribeToTask requests must use the JSON-RPC envelope.",
                {"failures": failures, "method": payload.get("method")},
            )
        return _passed(
            "task-resubscribe-jsonrpc-envelope",
            "Task resubscribe JSON-RPC envelope",
            {"method": payload.get("method"), "hasId": "id" in payload},
        )

    def _check_task_resubscribe_event_shape(
        self,
        payload: Mapping[str, object],
    ) -> A2AConformanceFinding:
        if not isinstance(payload.get("statusUpdate"), Mapping):
            return _failed(
                "task-resubscribe-event-shape",
                "Task resubscribe event shape",
                "task resubscribe events must use statusUpdate.",
            )
        try:
            event = a2a_task_subscription_event_from_dict(payload)
        except Exception as error:
            return _failed(
                "task-resubscribe-event-shape",
                "Task resubscribe event shape",
                f"statusUpdate payload must parse as a task subscription event: {error}",
            )
        if not event.task.task_id:
            return _failed(
                "task-resubscribe-event-shape",
                "Task resubscribe event shape",
                "statusUpdate payload must include a task id.",
            )
        return _passed(
            "task-resubscribe-event-shape",
            "Task resubscribe event shape",
            {"taskId": event.task.task_id, "eventId": event.event_id},
        )

    def _check_protocol_version(
        self,
        policy: A2AProtocolVersionPolicy,
        headers: Mapping[str, str] | None,
    ) -> A2AConformanceFinding:
        try:
            requested = policy.requested_version(headers)
            policy.ensure_supported(headers)
        except A2AProtocolVersionError as error:
            return _failed(
                "protocol-version",
                "Protocol version policy",
                str(error),
                {
                    "requestedVersion": error.requested_version,
                    "supportedVersions": list(error.supported_versions),
                },
            )
        return _passed(
            "protocol-version",
            "Protocol version policy",
            {
                "requestedVersion": requested,
                "defaultClientVersion": policy.default_client_version,
                "supportedVersions": list(policy.supported_versions),
            },
        )

    def _check_extension_negotiation(
        self,
        policy: A2AExtensionNegotiationPolicy,
        card: A2AAgentCard,
        headers: Mapping[str, str] | None,
    ) -> A2AConformanceFinding:
        try:
            requested = policy.requested_extensions(headers)
            outbound = policy.extension_header_for_card(card, headers=headers)
            inbound = policy.negotiate_inbound(headers)
        except A2AExtensionNegotiationError as error:
            return _failed(
                "extension-negotiation",
                "Extension negotiation",
                str(error),
                {
                    "missingExtensions": list(error.missing_extensions),
                    "requestedExtensions": list(error.requested_extensions),
                    "supportedExtensions": list(error.supported_extensions),
                },
            )
        return _passed(
            "extension-negotiation",
            "Extension negotiation",
            {
                "requestedExtensions": list(requested),
                "acceptedExtensions": list(inbound.accepted_extensions),
                "unsupportedExtensions": list(inbound.unsupported_extensions),
                "outboundHeader": outbound,
            },
        )

    def _check_extension_required_error_mapping(
        self,
        policy: A2AExtensionNegotiationPolicy,
        operation_payload: Mapping[str, object],
        headers: Mapping[str, str] | None,
        version_policy: A2AProtocolVersionPolicy,
    ) -> A2AConformanceFinding:
        server = A2AOperationServer(
            _StaticA2AOperationRunner(),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
            protocol_version_policy=version_policy,
            extension_negotiation_policy=policy,
        )
        response = server.handle_operation(operation_payload, headers=headers)
        error = response.get("error")
        if not isinstance(error, Mapping):
            return _passed(
                "extension-required-error-mapping",
                "Extension required error mapping",
                {"errorCode": None, "missingExtensions": []},
            )
        if error.get("code") != -32008:
            return _failed(
                "extension-required-error-mapping",
                "Extension required error mapping",
                "Required extension failures must map to JSON-RPC -32008.",
                {"errorCode": error.get("code")},
            )
        data = error.get("data")
        missing: object = []
        if isinstance(data, Mapping):
            missing = data.get("missingExtensions", [])
        return _passed(
            "extension-required-error-mapping",
            "Extension required error mapping",
            {"errorCode": -32008, "missingExtensions": list(missing)},
        )


@dataclass(frozen=True, slots=True)
class _A2ASamplePayloads:
    message_payload: Mapping[str, object]
    artifact_payload: Mapping[str, object]
    status_event_payload: Mapping[str, object]
    artifact_event_payload: Mapping[str, object]
    operation_request_payload: Mapping[str, object]
    stream_operation_request_payload: Mapping[str, object]
    stream_event_payload: Mapping[str, object]
    task_resubscribe_request_payload: Mapping[str, object]
    task_resubscribe_event_payload: Mapping[str, object]

    @classmethod
    def build(
        cls,
        *,
        message_payload: Mapping[str, object] | None,
        artifact_payload: Mapping[str, object] | None,
        status_event_payload: Mapping[str, object] | None,
        artifact_event_payload: Mapping[str, object] | None,
        operation_request_payload: Mapping[str, object] | None,
        stream_operation_request_payload: Mapping[str, object] | None,
        stream_event_payload: Mapping[str, object] | None,
        task_resubscribe_request_payload: Mapping[str, object] | None,
        task_resubscribe_event_payload: Mapping[str, object] | None,
    ) -> _A2ASamplePayloads:
        message = _sample_message()
        artifact = _sample_artifact()
        task = A2ATask(
            task_id="task_1",
            context_id="ctx_1",
            state="working",
            messages=(message,),
            artifacts=(artifact,),
        )
        return cls(
            message_payload=message_payload or _message_payload(message),
            artifact_payload=artifact_payload or a2a_artifact_to_dict(artifact),
            status_event_payload=(
                status_event_payload
                or a2a_task_subscription_event_to_dict(
                    A2ATaskSubscriptionEvent(event_id="1", task=task),
                )
            ),
            artifact_event_payload=(
                artifact_event_payload
                or a2a_task_artifact_update_event_to_dict(
                    A2ATaskArtifactUpdateEvent(
                        event_id="2",
                        task_id="task_1",
                        context_id="ctx_1",
                        artifact=artifact,
                    ),
                )
            ),
            operation_request_payload=(
                operation_request_payload
                or a2a_operation_request_to_dict(
                    A2AOperationRequest.message_send(
                        message,
                        request_id="req_1",
                    ),
                )
            ),
            stream_operation_request_payload=(
                stream_operation_request_payload
                or a2a_operation_request_to_dict(
                    A2AOperationRequest.message_stream(
                        message,
                        request_id="stream_req_1",
                    ),
                )
            ),
            stream_event_payload=(
                stream_event_payload
                or {"task": _a2a_task_to_dict_for_conformance(task)}
            ),
            task_resubscribe_request_payload=(
                task_resubscribe_request_payload
                or a2a_operation_request_to_dict(
                    A2AOperationRequest.task_resubscribe(
                        "task_1",
                        after_event_id=1,
                        request_id="task_resubscribe_req_1",
                    ),
                )
            ),
            task_resubscribe_event_payload=(
                task_resubscribe_event_payload
                or a2a_task_subscription_event_to_dict(
                    A2ATaskSubscriptionEvent(event_id="2", task=task),
                )
            ),
        )


class _StaticA2AOperationRunner:
    def send_message(self, message: A2AMessage) -> A2ATask:
        return A2ATask(
            task_id=message.task_id or "task_1",
            context_id=message.context_id,
            state="completed",
            messages=(message,),
        )


def _sample_message() -> A2AMessage:
    return A2AMessage(
        role="user",
        parts=(
            A2AMessagePart.from_text("hello"),
            A2AMessagePart.from_file_bytes(
                "aGVsbG8=",
                filename="hello.txt",
                media_type="text/plain",
            ),
            A2AMessagePart.from_file_url(
                "https://files.example/report.pdf",
                filename="report.pdf",
                media_type="application/pdf",
            ),
            A2AMessagePart.from_data(
                {"topic": "conformance"},
                media_type="application/json",
            ),
        ),
        message_id="msg_1",
        context_id="ctx_1",
        task_id="task_1",
    )


def _sample_artifact() -> A2AArtifact:
    return A2AArtifact(
        artifact_id="artifact_1",
        parts=(
            A2AMessagePart.from_text("summary"),
            A2AMessagePart.from_data(
                {"path": "artifact.txt"},
                media_type="application/json",
            ),
        ),
        name="Result bundle",
    )


def _message_payload(message: A2AMessage) -> Mapping[str, object]:
    return A2AOperationRequest.message_send(message).params["message"]  # type: ignore[return-value]


def _a2a_task_to_dict_for_conformance(task: A2ATask) -> Mapping[str, object]:
    return {
        "id": task.task_id,
        "contextId": task.context_id,
        "status": {"state": task.state},
        "messages": [_message_payload(message) for message in task.messages],
        "artifacts": [
            a2a_artifact_to_dict(artifact)
            for artifact in task.artifacts
        ],
    }


def _sse_event_chunk(payload: Mapping[str, object]) -> str:
    return f"event: task\ndata: {json.dumps(dict(payload))}\n\n"


def _part_wrapper_failures(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ["parts must be a non-empty list"]
    if not value:
        return ["parts must be a non-empty list"]
    failures: list[str] = []
    for index, part in enumerate(value):
        if not isinstance(part, Mapping):
            failures.append(f"parts[{index}] must be an object")
            continue
        if "kind" in part:
            failures.append(f"parts[{index}] must not include kind")
        payload_keys = [
            name for name in ("text", "raw", "url", "data")
            if name in part
        ]
        if len(payload_keys) != 1:
            failures.append(
                f"parts[{index}] must include exactly one payload wrapper",
            )
    return failures


def _passed(
    check_id: str,
    title: str,
    evidence: Mapping[str, object] | None = None,
) -> A2AConformanceFinding:
    return A2AConformanceFinding(
        check_id=check_id,
        title=title,
        passed=True,
        evidence=dict(evidence or {}),
    )


def _failed(
    check_id: str,
    title: str,
    detail: str,
    evidence: Mapping[str, object] | None = None,
) -> A2AConformanceFinding:
    return A2AConformanceFinding(
        check_id=check_id,
        title=title,
        passed=False,
        detail=detail,
        evidence=dict(evidence or {}),
    )
