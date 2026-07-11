from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

import agentos
import pytest
from agentos.release import (
    RELEASE_EVIDENCE_REQUIRED_GATES,
    ReleaseEvidenceValidationReport,
    validate_release_candidate_evidence_manifest,
    validate_release_evidence_manifest,
)

from tests._release_evidence_fixtures import passing_gate, release_manifest


ROOT = Path(__file__).resolve().parents[1]
RELEASE_EVIDENCE = ROOT / "docs" / "release-evidence.json"
RELEASE_EVIDENCE_EXAMPLE = ROOT / "docs" / "release-evidence.example.json"
LOCAL_RELEASE_EVIDENCE_ENV = "AGENTOS_VALIDATE_LOCAL_RELEASE_EVIDENCE"
TEST_RELEASE_IDENTITY = (
    "review/agentos-sdk-architecture-20260611",
    "abc123",
    "0.1.0rc1",
)


@pytest.fixture
def fixed_release_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str, str]:
    monkeypatch.setattr(
        sys.modules[__name__],
        "current_release_identity",
        lambda: TEST_RELEASE_IDENTITY,
    )
    return TEST_RELEASE_IDENTITY


def current_release_identity() -> tuple[str, str, str]:
    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    return branch, commit, agentos.__version__


def _local_release_evidence_validation_enabled() -> bool:
    return os.environ.get(LOCAL_RELEASE_EVIDENCE_ENV) == "1"


def _validate_local_release_evidence_if_requested(
    manifest_path: Path,
) -> ReleaseEvidenceValidationReport | None:
    if not _local_release_evidence_validation_enabled():
        return None
    if not manifest_path.exists():
        pytest.fail(f"release evidence manifest missing: {manifest_path}")

    try:
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except UnicodeError:
        pytest.fail(
            "release evidence manifest unreadable: invalid UTF-8",
            pytrace=False,
        )
    except json.JSONDecodeError:
        pytest.fail(
            "release evidence manifest unreadable: invalid JSON",
            pytrace=False,
        )
    except OSError as exc:
        pytest.fail(f"release evidence manifest unreadable: {exc}", pytrace=False)
    if not isinstance(manifest_value, Mapping):
        pytest.fail(
            "release evidence manifest must be a JSON object",
            pytrace=False,
        )
    manifest = dict(manifest_value)
    branch, commit, version = current_release_identity()
    return validate_release_candidate_evidence_manifest(
        manifest,
        expected_branch=branch,
        expected_commit=commit,
        expected_version=version,
    )


def test_local_release_evidence_is_not_an_implicit_unit_test_input(
    fixed_release_identity: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text("not-json", encoding="utf-8")
    monkeypatch.delenv(LOCAL_RELEASE_EVIDENCE_ENV, raising=False)

    assert _validate_local_release_evidence_if_requested(manifest) is None


def test_local_release_evidence_validation_accepts_matching_identity_when_enabled(
    fixed_release_identity: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    branch, commit, version = fixed_release_identity
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text(
        json.dumps(
            release_manifest(
                release_candidate={
                    "branch": branch,
                    "generated_at": "2026-07-11T01:00:00+08:00",
                    "commit": commit,
                    "version": version,
                },
            ),
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(LOCAL_RELEASE_EVIDENCE_ENV, "1")
    monkeypatch.setenv("PATH", "")

    report = _validate_local_release_evidence_if_requested(manifest)

    assert report is not None
    assert report.accepted is True


def test_local_release_evidence_validation_requires_manifest_when_enabled(
    fixed_release_identity: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(LOCAL_RELEASE_EVIDENCE_ENV, "1")

    with pytest.raises(pytest.fail.Exception, match="release evidence manifest missing"):
        _validate_local_release_evidence_if_requested(
            tmp_path / "missing-release-evidence.json",
        )


@pytest.mark.parametrize(
    ("payload", "diagnostic"),
    [
        pytest.param(b"\xff", "invalid UTF-8", id="invalid-utf8"),
        pytest.param(b"{", "invalid JSON", id="invalid-json"),
    ],
)
def test_local_release_evidence_validation_reports_unreadable_input(
    fixed_release_identity: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes,
    diagnostic: str,
) -> None:
    manifest = tmp_path / "release-evidence.json"
    manifest.write_bytes(payload)
    monkeypatch.setenv(LOCAL_RELEASE_EVIDENCE_ENV, "1")

    with pytest.raises(pytest.fail.Exception, match=diagnostic):
        _validate_local_release_evidence_if_requested(manifest)


@pytest.mark.parametrize("payload", ["null", "[]"])
def test_local_release_evidence_validation_requires_json_object(
    fixed_release_identity: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: str,
) -> None:
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text(payload, encoding="utf-8")
    monkeypatch.setenv(LOCAL_RELEASE_EVIDENCE_ENV, "1")

    with pytest.raises(pytest.fail.Exception, match="must be a JSON object"):
        _validate_local_release_evidence_if_requested(manifest)


def test_local_release_evidence_validation_rejects_identity_drift_when_enabled(
    fixed_release_identity: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    branch, _, version = fixed_release_identity
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text(
        json.dumps(
            release_manifest(
                release_candidate={
                    "branch": branch,
                    "generated_at": "2026-07-11T01:00:00+08:00",
                    "commit": "old-commit",
                    "version": version,
                },
            ),
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(LOCAL_RELEASE_EVIDENCE_ENV, "1")

    report = _validate_local_release_evidence_if_requested(manifest)

    assert report is not None
    assert report.accepted is False
    assert any(
        "release_candidate.commit expected" in finding
        for finding in report.gate_evidence_findings
    )


def test_local_release_evidence_validation_preserves_blocking_gates_when_enabled(
    fixed_release_identity: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    branch, commit, version = fixed_release_identity
    payload = release_manifest(
        release_candidate={
            "branch": branch,
            "generated_at": "2026-07-11T01:00:00+08:00",
            "commit": commit,
            "version": version,
        },
        independent_review={
            "status": "pending",
            "ready_for_release_candidate": False,
            "evidence_ref": "fresh review not run",
        },
    )
    gates = payload["gates"]
    assert isinstance(gates, dict)
    gates["independent_review"] = {
        **passing_gate("independent_review"),
        "status": "pending",
    }
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(LOCAL_RELEASE_EVIDENCE_ENV, "1")

    report = _validate_local_release_evidence_if_requested(manifest)

    assert report is not None
    assert report.accepted is False
    assert report.blocking_gates == ("independent_review",)
    assert report.gate_evidence_findings == ()


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


def test_release_evidence_validator_rejects_overclaiming_manifest_identity() -> None:
    manifest = release_manifest(
        certification_claim="production-certified",
        sdk_owned=False,
        deployment_owned=[],
    )

    report = validate_release_evidence_manifest(manifest)

    assert report.accepted is False
    assert (
        "certification_claim must be sdk-release-candidate-evidence or non-certifying-sdk-evidence"
        in report.gate_evidence_findings
    )
    assert "sdk_owned must be true" in report.gate_evidence_findings
    assert (
        "deployment_owned must declare SDK/deployment boundary responsibilities"
        in report.gate_evidence_findings
    )


def test_release_evidence_validator_redacts_secret_patterns_in_neutral_fields() -> None:
    manifest = release_manifest(
        gates={
            **{
                name: passing_gate(name)
                for name in RELEASE_EVIDENCE_REQUIRED_GATES
            },
            "full_test_suite": {
                **passing_gate("full_test_suite"),
                "command": "curl -H 'Authorization: Bearer raw-token' https://ci",
                "evidence_ref": "https://ci.example/logs?api_key=sk-live-secret",
            },
        },
        notes=["probe printed postgres://user:secret@db/agentos"],
    )

    report = validate_release_evidence_manifest(manifest)
    payload_text = json.dumps(report.as_dict())

    assert report.accepted is False
    assert "secret-like value at gates.full_test_suite.command" in (
        report.secret_findings
    )
    assert "secret-like value at gates.full_test_suite.evidence_ref" in (
        report.secret_findings
    )
    assert "secret-like value at notes[0]" in report.secret_findings
    assert "raw-token" not in payload_text
    assert "sk-live-secret" not in payload_text
    assert "user:secret" not in payload_text
    assert "<redacted>" in payload_text


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
    report = _validate_local_release_evidence_if_requested(RELEASE_EVIDENCE)
    if report is None:
        pytest.skip(
            f"set {LOCAL_RELEASE_EVIDENCE_ENV}=1 for local candidate validation",
        )

    manifest = json.loads(RELEASE_EVIDENCE.read_text(encoding="utf-8"))
    if manifest["independent_review"]["status"] == "passed":
        assert report.accepted is True
    else:
        assert report.accepted is False
        assert report.blocking_gates == ("independent_review",)
        assert report.missing_gates == ()
        assert report.gate_evidence_findings == ()
        assert report.secret_findings == ()


def test_release_candidate_evidence_gate_requires_expected_identity() -> None:
    from agentos.release import validate_release_candidate_evidence_manifest

    manifest = release_manifest()

    report = validate_release_candidate_evidence_manifest(
        manifest,
        expected_branch="",
        expected_commit="",
        expected_version="",
    )

    assert report.accepted is False
    assert (
        "release candidate validation requires expected branch"
        in report.gate_evidence_findings
    )
    assert (
        "release candidate validation requires expected commit"
        in report.gate_evidence_findings
    )
    assert (
        "release candidate validation requires expected version"
        in report.gate_evidence_findings
    )


def test_release_candidate_evidence_gate_accepts_matching_identity() -> None:
    from agentos.release import validate_release_candidate_evidence_manifest

    report = validate_release_candidate_evidence_manifest(
        release_manifest(),
        expected_branch="review/agentos-sdk-architecture-20260611",
        expected_commit="abc123",
        expected_version="0.1.0rc1",
    )

    assert report.accepted is True
    assert report.gate_evidence_findings == ()


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


def test_release_evidence_templates_include_committed_generator_metadata() -> None:
    for manifest_path in (RELEASE_EVIDENCE_EXAMPLE,):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert manifest["generator"] == {
            "source_path": "scripts/generate_release_evidence.py",
            "review_policy": "does_not_mark_independent_review_passed",
        }
