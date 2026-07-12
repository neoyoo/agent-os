from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import agentos
import pytest
from agentos.release import (
    ReleaseEvidenceValidationReport,
    validate_release_candidate_evidence_manifest,
)

from tests._release_evidence_fixtures import passing_gate, release_manifest


ROOT = Path(__file__).resolve().parents[1]
RELEASE_EVIDENCE = ROOT / "docs" / "release-evidence.json"
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
