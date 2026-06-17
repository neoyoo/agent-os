from __future__ import annotations

import json
import tomllib
from pathlib import Path

import agentos
from agentos.release import (
    RELEASE_EVIDENCE_REQUIRED_GATES,
    validate_release_evidence_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
RELEASE_EVIDENCE = ROOT / "docs" / "release-evidence.json"
RELEASE_EVIDENCE_EXAMPLE = ROOT / "docs" / "release-evidence.example.json"


def passing_gate(name: str) -> dict[str, object]:
    return {
        "status": "passed",
        "command": f"run {name}",
        "evidence_ref": f"ci://agentos/{name}",
        "required": True,
        "last_verified_at": "2026-06-17T01:00:00+08:00",
    }


def release_manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema": "agentos.release_evidence",
        "schema_version": 1,
        "certification_claim": "sdk-release-candidate-evidence",
        "sdk_owned": True,
        "release_candidate": {
            "branch": "review/agentos-sdk-architecture-20260611",
            "generated_at": "2026-06-17T01:00:00+08:00",
            "commit": "abc123",
            "version": "0.1.0rc1",
        },
        "deployment_owned": [
            "CI/CD execution",
            "artifact signing",
            "publishing",
            "deployment approval",
            "rollout and rollback",
        ],
        "independent_review": {
            "status": "passed",
            "ready_for_release_candidate": True,
            "evidence_ref": "review://fresh-subagent",
        },
        "live_backend_verification": {
            "status": "not_certified_by_sdk_rc",
            "sdk_rc_claim": "boundary_only",
            "required_before_production_deployment": True,
            "required_backends": (
                "agent_registry",
                "message_queue",
                "task_store",
                "plan_store",
                "worker_process_supervisor",
                "session_snapshot_persistence",
            ),
            "evidence_ref": "docs/production-readiness.md#live-backend-verification-evidence-boundary",
        },
        "gates": {
            name: passing_gate(name)
            for name in RELEASE_EVIDENCE_REQUIRED_GATES
        },
        "notes": ["SDK-side release evidence only."],
    }
    manifest.update(overrides)
    return manifest


def test_release_evidence_validator_blocks_pending_independent_review() -> None:
    manifest = release_manifest()
    gates = manifest["gates"]
    assert isinstance(gates, dict)
    gates["independent_review"] = {
        **passing_gate("independent_review"),
        "status": "pending",
        "evidence_ref": "fresh review not run",
    }
    manifest["independent_review"] = {
        "status": "pending",
        "ready_for_release_candidate": False,
        "evidence_ref": "fresh review not run",
    }

    report = validate_release_evidence_manifest(manifest)
    payload = report.as_dict()

    assert report.accepted is False
    assert "independent_review" in report.blocking_gates
    assert payload["block_release_candidate"] is True
    assert payload["status"] == "failed"
    assert "fresh review not run" in json.dumps(payload)


def test_release_evidence_validator_requires_all_hard_gates() -> None:
    manifest = release_manifest()
    gates = manifest["gates"]
    assert isinstance(gates, dict)
    del gates["runtime_boundary_scan"]

    report = validate_release_evidence_manifest(manifest)

    assert report.accepted is False
    assert report.missing_gates == ("runtime_boundary_scan",)
    assert report.blocking_gates == ()


def test_release_evidence_validator_rejects_hollow_passed_gates() -> None:
    manifest = release_manifest(
        gates={
            name: {"status": "passed"}
            for name in RELEASE_EVIDENCE_REQUIRED_GATES
        },
    )

    report = validate_release_evidence_manifest(manifest)
    payload = report.as_dict()

    assert report.accepted is False
    assert "full_test_suite" in report.blocking_gates
    assert "gate_evidence_findings" in payload
    assert (
        "gates.full_test_suite.command must be a non-placeholder string"
        in payload["gate_evidence_findings"]
    )
    assert (
        "gates.full_test_suite.required must be true"
        in payload["gate_evidence_findings"]
    )


def test_release_evidence_validator_rejects_non_pep440_candidate_version() -> None:
    manifest = release_manifest(
        release_candidate={
            "branch": "review/agentos-sdk-architecture-20260611",
            "generated_at": "2026-06-17T01:00:00+08:00",
            "commit": "abc123",
            "version": "0.1.0-rc.agentos-sdk-architecture-review",
        },
    )

    report = validate_release_evidence_manifest(manifest)

    assert report.accepted is False
    assert (
        "release_candidate.version must be a PEP 440-compatible release identifier"
        in report.gate_evidence_findings
    )


def test_release_evidence_validator_blocks_candidate_identity_drift() -> None:
    manifest = release_manifest()

    report = validate_release_evidence_manifest(
        manifest,
        expected_branch="review/agentos-sdk-architecture-20260611",
        expected_commit="def456",
        expected_version="0.1.0rc1",
    )

    assert report.accepted is False
    assert (
        "release_candidate.commit expected def456 but found abc123"
        in report.gate_evidence_findings
    )


def test_release_evidence_validator_blocks_gate_result_drift() -> None:
    manifest = release_manifest()
    gates = manifest["gates"]
    assert isinstance(gates, dict)
    gates["full_test_suite"] = {
        **passing_gate("full_test_suite"),
        "result": {
            "exit_code": 0,
            "passed": 1310,
            "skipped": 8,
        },
    }

    report = validate_release_evidence_manifest(
        manifest,
        expected_gate_results={
            "full_test_suite": {
                "exit_code": 0,
                "passed": 1357,
                "skipped": 8,
            },
        },
    )

    assert report.accepted is False
    assert "full_test_suite" in report.blocking_gates
    assert (
        "gates.full_test_suite.result.passed expected 1357 but found 1310"
        in report.gate_evidence_findings
    )


def test_release_evidence_validator_requires_live_backend_scope_decision() -> None:
    manifest = release_manifest()
    del manifest["live_backend_verification"]

    report = validate_release_evidence_manifest(manifest)

    assert report.accepted is False
    assert (
        "live_backend_verification must declare certified or boundary-only scope"
        in report.gate_evidence_findings
    )


def test_release_evidence_validator_rejects_secret_like_values() -> None:
    manifest = release_manifest(
        gates={
            **{
                name: passing_gate(name)
                for name in RELEASE_EVIDENCE_REQUIRED_GATES
            },
            "full_test_suite": {
                **passing_gate("full_test_suite"),
                "metadata": {"api_token": "sk-live-secret"},
            },
        },
    )

    report = validate_release_evidence_manifest(manifest)
    payload = report.as_dict()

    assert report.accepted is False
    assert "secret-like value at gates.full_test_suite.metadata.api_token" in (
        report.secret_findings
    )
    assert "sk-live-secret" not in json.dumps(payload)
    assert "<redacted>" in json.dumps(payload)


def test_release_evidence_validator_accepts_complete_manifest() -> None:
    report = validate_release_evidence_manifest(release_manifest())

    assert report.accepted is True
    assert report.missing_gates == ()
    assert report.blocking_gates == ()
    assert report.secret_findings == ()
    assert report.as_dict()["status"] == "ok"


def test_package_and_release_manifest_versions_match() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == agentos.__version__ == "0.1.0rc1"


def test_generated_release_evidence_is_ignored_not_source_tracked() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "docs/release-evidence.json" in gitignore


def test_generated_release_evidence_artifact_is_validated_when_present() -> None:
    if not RELEASE_EVIDENCE.exists():
        return
    manifest = json.loads(RELEASE_EVIDENCE.read_text(encoding="utf-8"))

    assert manifest["release_candidate"]["version"] == agentos.__version__
    report = validate_release_evidence_manifest(manifest)

    if manifest["independent_review"]["status"] == "passed":
        assert report.accepted is True
    else:
        assert report.accepted is False
        assert report.blocking_gates == ("independent_review",)
        assert report.missing_gates == ()
        assert report.gate_evidence_findings == ()
        assert report.secret_findings == ()


def test_release_evidence_templates_cover_validator_required_gate_set() -> None:
    for manifest_path in (RELEASE_EVIDENCE_EXAMPLE,):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        gates = manifest["gates"]

        assert set(RELEASE_EVIDENCE_REQUIRED_GATES) <= set(gates), manifest_path.name


def test_release_evidence_templates_include_top_level_independent_review() -> None:
    for manifest_path in (RELEASE_EVIDENCE_EXAMPLE,):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        review = manifest["independent_review"]

        assert review["status"] in {"unknown", "pending", "passed", "failed"}
        assert isinstance(review["evidence_ref"], str)
        assert isinstance(review["ready_for_release_candidate"], bool)
