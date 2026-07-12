from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import re

from agentos._redaction import redact_secret_patterns


ReleaseEvidenceGateStatus = Literal["unknown", "pending", "passed", "failed"]

RELEASE_EVIDENCE_REQUIRED_GATES: tuple[str, ...] = (
    "public_api_audit",
    "full_test_suite",
    "compileall",
    "diff_hygiene",
    "runtime_boundary_scan",
    "docs_alignment",
    "migration_index",
    "api_stability_inventory",
    "production_reference_honesty",
    "planner_plan_store_concurrency",
    "workspace_security_policy",
    "independent_review",
)

_RELEASE_EVIDENCE_STATUSES = frozenset(
    {"unknown", "pending", "passed", "failed"},
)
_SECRET_KEY_PARTS = frozenset(
    {
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    },
)
_PEP440_RELEASE_RE = re.compile(
    r"^[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
    r"(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?$",
    re.IGNORECASE,
)
_LIVE_BACKEND_SCOPE_STATUSES = frozenset(
    {
        "certified",
        "not_certified_by_sdk_rc",
    },
)
_LIVE_BACKEND_REQUIRED_BACKENDS = (
    "agent_registry",
    "message_queue",
    "task_store",
    "plan_store",
    "worker_process_supervisor",
    "session_snapshot_persistence",
)
_ALLOWED_CERTIFICATION_CLAIMS = frozenset(
    {
        "sdk-release-candidate-evidence",
        "non-certifying-sdk-evidence",
    },
)
_REQUIRED_DEPLOYMENT_BOUNDARIES = frozenset(
    {
        "CI/CD execution",
        "artifact signing",
        "publishing",
        "deployment approval",
        "rollout and rollback",
    },
)


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceValidationReport:
    """JSON-safe validation result for one release evidence manifest."""

    accepted: bool
    missing_gates: tuple[str, ...]
    blocking_gates: tuple[str, ...]
    gate_evidence_findings: tuple[str, ...]
    secret_findings: tuple[str, ...]
    gate_statuses: Mapping[str, str]
    redacted_manifest: Mapping[str, object]
    schema: str = "agentos.release_evidence.validation"
    schema_version: int = 1

    @property
    def block_release_candidate(self) -> bool:
        """Return whether this report blocks release-candidate promotion."""

        return not self.accepted

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe validation payload."""

        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "status": "ok" if self.accepted else "failed",
            "accepted": self.accepted,
            "block_release_candidate": self.block_release_candidate,
            "required_gates": RELEASE_EVIDENCE_REQUIRED_GATES,
            "missing_gates": self.missing_gates,
            "blocking_gates": self.blocking_gates,
            "gate_evidence_findings": self.gate_evidence_findings,
            "secret_findings": self.secret_findings,
            "gate_statuses": dict(self.gate_statuses),
            "manifest": _json_safe_mapping(self.redacted_manifest),
            "sdk_owned": (
                "ReleaseEvidenceValidationReport",
                "validate_release_evidence_manifest",
                "machine-readable release evidence gate",
                "secret-like value redaction",
            ),
            "deployment_owned": (
                "CI/CD execution",
                "artifact signing",
                "publishing",
                "deployment approval",
                "rollout and rollback",
            ),
        }


def validate_release_evidence_manifest(
    manifest: Mapping[str, object],
    *,
    required_gates: tuple[str, ...] = RELEASE_EVIDENCE_REQUIRED_GATES,
    expected_branch: str | None = None,
    expected_commit: str | None = None,
    expected_version: str | None = None,
    expected_gate_results: Mapping[str, Mapping[str, object]] | None = None,
) -> ReleaseEvidenceValidationReport:
    """Validate release evidence without executing release commands."""

    gates = manifest.get("gates")
    gate_mapping = gates if isinstance(gates, Mapping) else {}
    missing_gates = tuple(name for name in required_gates if name not in gate_mapping)
    expected_results = expected_gate_results or {}

    gate_statuses: dict[str, str] = {}
    blocking_gates: list[str] = []
    gate_evidence_findings: list[str] = list(
        _manifest_identity_findings(manifest),
    )
    gate_evidence_findings.extend(
        _release_candidate_findings(
            manifest,
            expected_branch=expected_branch,
            expected_commit=expected_commit,
            expected_version=expected_version,
        ),
    )
    gate_evidence_findings.extend(_live_backend_verification_findings(manifest))
    for gate_name, gate_payload in gate_mapping.items():
        status = _gate_status(gate_payload)
        gate_name_text = str(gate_name)
        gate_statuses[gate_name_text] = status
        if gate_name_text in required_gates:
            findings = _gate_evidence_findings(
                gate_name_text,
                gate_payload,
                expected_result=expected_results.get(gate_name_text),
            )
            gate_evidence_findings.extend(findings)
            if (status != "passed" or findings) and gate_name_text not in blocking_gates:
                blocking_gates.append(gate_name_text)

    if "independent_review" in required_gates:
        review_status = _independent_review_status(manifest)
        gate_statuses["independent_review"] = review_status
        if review_status != "passed" and "independent_review" not in blocking_gates:
            blocking_gates.append("independent_review")

    redacted_manifest, secret_findings = _redact_with_findings(manifest)
    accepted = (
        not missing_gates
        and not blocking_gates
        and not gate_evidence_findings
        and not secret_findings
    )
    return ReleaseEvidenceValidationReport(
        accepted=accepted,
        missing_gates=missing_gates,
        blocking_gates=tuple(blocking_gates),
        gate_evidence_findings=tuple(gate_evidence_findings),
        secret_findings=tuple(secret_findings),
        gate_statuses=gate_statuses,
        redacted_manifest=redacted_manifest,
    )


def validate_release_candidate_evidence_manifest(
    manifest: Mapping[str, object],
    *,
    expected_branch: str,
    expected_commit: str,
    expected_version: str,
    required_gates: tuple[str, ...] = RELEASE_EVIDENCE_REQUIRED_GATES,
    expected_gate_results: Mapping[str, Mapping[str, object]] | None = None,
) -> ReleaseEvidenceValidationReport:
    """Validate release-candidate evidence with mandatory source identity."""

    identity_findings: list[str] = []
    branch = expected_branch if _is_non_placeholder_string(expected_branch) else None
    commit = expected_commit if _is_non_placeholder_string(expected_commit) else None
    version = expected_version if _is_non_placeholder_string(expected_version) else None
    if branch is None:
        identity_findings.append(
            "release candidate validation requires expected branch",
        )
    if commit is None:
        identity_findings.append(
            "release candidate validation requires expected commit",
        )
    if version is None:
        identity_findings.append(
            "release candidate validation requires expected version",
        )

    report = validate_release_evidence_manifest(
        manifest,
        required_gates=required_gates,
        expected_branch=branch,
        expected_commit=commit,
        expected_version=version,
        expected_gate_results=expected_gate_results,
    )
    if not identity_findings:
        return report
    return ReleaseEvidenceValidationReport(
        accepted=False,
        missing_gates=report.missing_gates,
        blocking_gates=report.blocking_gates,
        gate_evidence_findings=tuple(identity_findings)
        + report.gate_evidence_findings,
        secret_findings=report.secret_findings,
        gate_statuses=report.gate_statuses,
        redacted_manifest=report.redacted_manifest,
    )


def _manifest_identity_findings(manifest: Mapping[str, object]) -> tuple[str, ...]:
    findings: list[str] = []
    if manifest.get("schema") != "agentos.release_evidence":
        findings.append("schema must be agentos.release_evidence")
    if manifest.get("schema_version") != 1:
        findings.append("schema_version must be 1")
    if manifest.get("certification_claim") not in _ALLOWED_CERTIFICATION_CLAIMS:
        findings.append(
            "certification_claim must be sdk-release-candidate-evidence or non-certifying-sdk-evidence",
        )
    if manifest.get("sdk_owned") is not True:
        findings.append("sdk_owned must be true")
    deployment_owned = manifest.get("deployment_owned")
    if not isinstance(deployment_owned, tuple | list) or not (
        _REQUIRED_DEPLOYMENT_BOUNDARIES <= {str(item) for item in deployment_owned}
    ):
        findings.append(
            "deployment_owned must declare SDK/deployment boundary responsibilities",
        )
    return tuple(findings)


def _release_candidate_findings(
    manifest: Mapping[str, object],
    *,
    expected_branch: str | None,
    expected_commit: str | None,
    expected_version: str | None,
) -> tuple[str, ...]:
    candidate = manifest.get("release_candidate")
    if not isinstance(candidate, Mapping):
        return ("release_candidate must be an object",)

    findings: list[str] = []
    for field_name in ("branch", "generated_at", "commit", "version"):
        value = candidate.get(field_name)
        if not _is_non_placeholder_string(value):
            findings.append(
                f"release_candidate.{field_name} must be a non-placeholder string",
            )
    version = candidate.get("version")
    if isinstance(version, str) and not _is_pep440_release_identifier(version):
        findings.append(
            "release_candidate.version must be a PEP 440-compatible release identifier",
        )
    comparisons = {
        "branch": expected_branch,
        "commit": expected_commit,
        "version": expected_version,
    }
    for field_name, expected in comparisons.items():
        if expected is None:
            continue
        actual = candidate.get(field_name)
        if actual != expected:
            findings.append(
                f"release_candidate.{field_name} expected {expected} but found {actual}",
            )
    return tuple(findings)


def _live_backend_verification_findings(
    manifest: Mapping[str, object],
) -> tuple[str, ...]:
    verification = manifest.get("live_backend_verification")
    if not isinstance(verification, Mapping):
        return (
            "live_backend_verification must declare certified or boundary-only scope",
        )
    findings: list[str] = []
    status = verification.get("status")
    if not isinstance(status, str) or status not in _LIVE_BACKEND_SCOPE_STATUSES:
        findings.append(
            "live_backend_verification.status must be certified or not_certified_by_sdk_rc",
        )
    claim = verification.get("sdk_rc_claim")
    if status == "certified":
        if claim != "live_backend_certified":
            findings.append(
                "live_backend_verification.sdk_rc_claim must be live_backend_certified when status is certified",
            )
    elif status == "not_certified_by_sdk_rc":
        if claim != "boundary_only":
            findings.append(
                "live_backend_verification.sdk_rc_claim must be boundary_only when status is not_certified_by_sdk_rc",
            )
        if verification.get("required_before_production_deployment") is not True:
            findings.append(
                "live_backend_verification.required_before_production_deployment must be true for boundary-only RC scope",
            )
    backends = verification.get("required_backends")
    if not isinstance(backends, tuple | list) or tuple(backends) != (
        _LIVE_BACKEND_REQUIRED_BACKENDS
    ):
        findings.append(
            "live_backend_verification.required_backends must cover the state-plane backend set",
        )
    if not _is_non_placeholder_string(verification.get("evidence_ref")):
        findings.append(
            "live_backend_verification.evidence_ref must be a non-placeholder string",
        )
    return tuple(findings)


def _gate_status(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return "unknown"
    status = payload.get("status")
    if isinstance(status, str):
        normalized = status.lower()
        if normalized in _RELEASE_EVIDENCE_STATUSES:
            return normalized
    return "unknown"


def _gate_evidence_findings(
    gate_name: str,
    payload: object,
    *,
    expected_result: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        return (f"gates.{gate_name} must be an object",)
    findings: list[str] = []
    required = payload.get("required")
    if required is not True:
        findings.append(f"gates.{gate_name}.required must be true")
    for field_name in ("command", "evidence_ref", "last_verified_at"):
        value = payload.get(field_name)
        if not _is_non_placeholder_string(value):
            findings.append(
                f"gates.{gate_name}.{field_name} must be a non-placeholder string",
            )
    if expected_result is not None:
        findings.extend(
            _gate_result_findings(
                gate_name,
                payload.get("result"),
                expected_result=expected_result,
            ),
        )
    return tuple(findings)


def _gate_result_findings(
    gate_name: str,
    result: object,
    *,
    expected_result: Mapping[str, object],
) -> tuple[str, ...]:
    if not isinstance(result, Mapping):
        return (f"gates.{gate_name}.result must be an object",)
    findings: list[str] = []
    for field_name, expected in expected_result.items():
        actual = result.get(field_name)
        if actual != expected:
            findings.append(
                f"gates.{gate_name}.result.{field_name} expected {expected} but found {actual}",
            )
    return tuple(findings)


def _is_non_placeholder_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    return not (stripped.startswith("<") and stripped.endswith(">"))


def _is_pep440_release_identifier(value: str) -> bool:
    return bool(_PEP440_RELEASE_RE.fullmatch(value.strip()))


def _independent_review_status(manifest: Mapping[str, object]) -> str:
    review = manifest.get("independent_review")
    if not isinstance(review, Mapping):
        return "unknown"
    status = review.get("status")
    ready = review.get("ready_for_release_candidate")
    if isinstance(status, str) and status.lower() == "passed" and ready is True:
        return "passed"
    if isinstance(status, str) and status.lower() in _RELEASE_EVIDENCE_STATUSES:
        return status.lower()
    return "unknown"


def _redact_with_findings(
    value: object,
    *,
    path: str = "",
    key_hint: str = "",
) -> tuple[object, list[str]]:
    if _is_secret_like_key(key_hint):
        return "<redacted>", (
            [f"secret-like value at {path}"] if _has_unredacted_value(value) else []
        )
    if isinstance(value, str):
        redacted = redact_secret_patterns(value)
        return redacted, (
            [f"secret-like value at {path}"] if redacted != value else []
        )
    if value is None or isinstance(value, int | float | bool):
        return value, []
    if isinstance(value, Path):
        return str(value), []
    if isinstance(value, Mapping):
        findings: list[str] = []
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            child_value, child_findings = _redact_with_findings(
                item,
                path=child_path,
                key_hint=key_text,
            )
            redacted[key_text] = child_value
            findings.extend(child_findings)
        return redacted, findings
    if isinstance(value, tuple | list):
        findings = []
        redacted_items = []
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]" if path else f"[{index}]"
            child_value, child_findings = _redact_with_findings(
                item,
                path=child_path,
            )
            redacted_items.append(child_value)
            findings.extend(child_findings)
        return tuple(redacted_items), findings
    return repr(value), []


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    safe, _ = _redact_with_findings(values)
    if isinstance(safe, Mapping):
        return dict(safe)
    return {"value": safe}


def _is_secret_like_key(key: str) -> bool:
    lowered = key.lower()
    parts = {
        part
        for part in lowered.replace("-", "_").replace(".", "_").split("_")
        if part
    }
    if lowered in _SECRET_KEY_PARTS:
        return True
    if parts & _SECRET_KEY_PARTS:
        return True
    return ("api" in parts and "key" in parts) or (
        "private" in parts and "key" in parts
    )


def _has_unredacted_value(value: object) -> bool:
    if value in (None, "", "<redacted>"):
        return False
    if isinstance(value, Mapping):
        return any(_has_unredacted_value(item) for item in value.values())
    if isinstance(value, tuple | list):
        return any(_has_unredacted_value(item) for item in value)
    return True


__all__ = [
    "RELEASE_EVIDENCE_REQUIRED_GATES",
    "ReleaseEvidenceGateStatus",
    "ReleaseEvidenceValidationReport",
    "validate_release_candidate_evidence_manifest",
    "validate_release_evidence_manifest",
]
