from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import agentos
import pytest
from agentos.release import RELEASE_EVIDENCE_REQUIRED_GATES

from tests._release_evidence_fixtures import passing_gate, release_manifest


ROOT = Path(__file__).resolve().parents[1]
RELEASE_EVIDENCE_GENERATOR = ROOT / "scripts" / "generate_release_evidence.py"
RELEASE_EVIDENCE_VALIDATOR = ROOT / "scripts" / "validate_release_evidence.py"


def release_evidence_generator_env(
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    source_path = str(ROOT / "src")
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        source_path if not pythonpath else os.pathsep.join((source_path, pythonpath))
    )
    return env


def run_release_evidence_validator(
    manifest: Path,
    *,
    branch: str,
    commit: str,
    version: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RELEASE_EVIDENCE_VALIDATOR),
            "--manifest",
            str(manifest),
            "--branch",
            branch,
            "--commit",
            commit,
            "--version",
            version,
        ],
        cwd=ROOT,
        env=release_evidence_generator_env(),
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_evidence_validator_cli_accepts_matching_manifest(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text(json.dumps(release_manifest()), encoding="utf-8")

    result = run_release_evidence_validator(
        manifest,
        branch="review/agentos-sdk-architecture-20260611",
        commit="abc123",
        version="0.1.0rc1",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["accepted"] is True


def test_release_evidence_validator_cli_rejects_missing_manifest(
    tmp_path: Path,
) -> None:
    result = run_release_evidence_validator(
        tmp_path / "missing-release-evidence.json",
        branch="review/agentos-sdk-architecture-20260611",
        commit="abc123",
        version="0.1.0rc1",
    )

    assert result.returncode == 2
    assert "release evidence manifest missing" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("payload", "diagnostic"),
    [
        pytest.param(b"\xff", "invalid UTF-8", id="invalid-utf8"),
        pytest.param(b"{", "invalid JSON", id="invalid-json"),
    ],
)
def test_release_evidence_validator_cli_reports_unreadable_input(
    tmp_path: Path,
    payload: bytes,
    diagnostic: str,
) -> None:
    manifest = tmp_path / "release-evidence.json"
    manifest.write_bytes(payload)

    result = run_release_evidence_validator(
        manifest,
        branch="review/agentos-sdk-architecture-20260611",
        commit="abc123",
        version="0.1.0rc1",
    )

    assert result.returncode == 2
    assert diagnostic in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("payload", ["null", "[]"])
def test_release_evidence_validator_cli_requires_json_object(
    tmp_path: Path,
    payload: str,
) -> None:
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text(payload, encoding="utf-8")

    result = run_release_evidence_validator(
        manifest,
        branch="review/agentos-sdk-architecture-20260611",
        commit="abc123",
        version="0.1.0rc1",
    )

    assert result.returncode == 2
    assert "release evidence manifest must be a JSON object" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_release_evidence_validator_cli_rejects_identity_drift(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text(json.dumps(release_manifest()), encoding="utf-8")

    result = run_release_evidence_validator(
        manifest,
        branch="review/agentos-sdk-architecture-20260611",
        commit="old-commit",
        version="0.1.0rc1",
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["accepted"] is False
    assert any(
        "release_candidate.commit expected old-commit but found abc123" in finding
        for finding in report["gate_evidence_findings"]
    )


def test_release_evidence_validator_cli_rejects_pending_independent_review_with_matching_identity(
    tmp_path: Path,
) -> None:
    payload = release_manifest(
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

    result = run_release_evidence_validator(
        manifest,
        branch="review/agentos-sdk-architecture-20260611",
        commit="abc123",
        version="0.1.0rc1",
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["blocking_gates"] == ["independent_review"]
    assert report["gate_evidence_findings"] == []


def test_release_evidence_generator_entrypoint_is_committed_and_reproducible(
    tmp_path: Path,
) -> None:
    output = tmp_path / "release-evidence.json"

    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE_EVIDENCE_GENERATOR),
            "--output",
            str(output),
            "--branch",
            "review/agentos-sdk-architecture-20260611",
            "--commit",
            "abc123",
            "--version",
            agentos.__version__,
            "--generated-at",
            "2026-06-17T01:00:00+08:00",
            "--independent-review-status",
            "pending",
            "--gate-result",
            "full_test_suite=0",
        ],
        cwd=ROOT,
        env=release_evidence_generator_env(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert manifest["schema"] == "agentos.release_evidence"
    assert manifest["schema_version"] == 1
    assert manifest["release_candidate"] == {
        "branch": "review/agentos-sdk-architecture-20260611",
        "generated_at": "2026-06-17T01:00:00+08:00",
        "commit": "abc123",
        "version": agentos.__version__,
    }
    assert manifest["generator"] == {
        "source_path": "scripts/generate_release_evidence.py",
        "review_policy": "does_not_mark_independent_review_passed",
    }
    assert set(RELEASE_EVIDENCE_REQUIRED_GATES) <= set(manifest["gates"])
    assert manifest["gates"]["full_test_suite"]["result"] == {"exit_code": 0}
    assert manifest["independent_review"]["status"] == "pending"
    assert manifest["independent_review"]["ready_for_release_candidate"] is False

    rerun = subprocess.run(
        [
            sys.executable,
            str(RELEASE_EVIDENCE_GENERATOR),
            "--output",
            str(tmp_path / "release-evidence-rerun.json"),
            "--branch",
            "review/agentos-sdk-architecture-20260611",
            "--commit",
            "abc123",
            "--version",
            agentos.__version__,
            "--generated-at",
            "2026-06-17T01:00:00+08:00",
            "--independent-review-status",
            "pending",
            "--gate-result",
            "full_test_suite=0",
        ],
        cwd=ROOT,
        env=release_evidence_generator_env(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert rerun.returncode == 0, rerun.stderr
    assert output.read_text(encoding="utf-8") == (
        tmp_path / "release-evidence-rerun.json"
    ).read_text(encoding="utf-8")


def test_release_evidence_generator_subprocess_has_source_pythonpath(
    tmp_path: Path,
) -> None:
    output = tmp_path / "release-evidence.json"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE_EVIDENCE_GENERATOR),
            "--output",
            str(output),
            "--branch",
            "review/agentos-sdk-architecture-20260611",
            "--commit",
            "abc123",
            "--version",
            agentos.__version__,
            "--generated-at",
            "2026-06-17T01:00:00+08:00",
            "--independent-review-status",
            "pending",
            "--gate-result",
            "full_test_suite=0",
        ],
        cwd=ROOT,
        env=release_evidence_generator_env(env),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["schema"] == (
        "agentos.release_evidence"
    )


def test_release_evidence_generator_treats_empty_runtime_boundary_scan_as_passed(
    tmp_path: Path,
) -> None:
    output = tmp_path / "release-evidence.json"
    matched_output = tmp_path / "release-evidence-matched.json"

    no_match = subprocess.run(
        [
            sys.executable,
            str(RELEASE_EVIDENCE_GENERATOR),
            "--output",
            str(output),
            "--branch",
            "review/agentos-sdk-architecture-20260611",
            "--commit",
            "abc123",
            "--version",
            agentos.__version__,
            "--generated-at",
            "2026-06-17T01:00:00+08:00",
            "--independent-review-status",
            "pending",
            "--gate-result",
            "runtime_boundary_scan=1",
        ],
        cwd=ROOT,
        env=release_evidence_generator_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    matched = subprocess.run(
        [
            sys.executable,
            str(RELEASE_EVIDENCE_GENERATOR),
            "--output",
            str(matched_output),
            "--branch",
            "review/agentos-sdk-architecture-20260611",
            "--commit",
            "abc123",
            "--version",
            agentos.__version__,
            "--generated-at",
            "2026-06-17T01:00:00+08:00",
            "--independent-review-status",
            "pending",
            "--gate-result",
            "runtime_boundary_scan=0",
        ],
        cwd=ROOT,
        env=release_evidence_generator_env(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert no_match.returncode == 0, no_match.stderr
    assert matched.returncode == 0, matched.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    matched_manifest = json.loads(matched_output.read_text(encoding="utf-8"))

    assert manifest["gates"]["runtime_boundary_scan"]["status"] == "passed"
    assert manifest["gates"]["runtime_boundary_scan"]["result"] == {
        "exit_code": 1,
        "matches": 0,
    }
    assert matched_manifest["gates"]["runtime_boundary_scan"]["status"] == "failed"


def test_release_evidence_generator_treats_runtime_boundary_scan_errors_as_failed(
    tmp_path: Path,
) -> None:
    output = tmp_path / "release-evidence.json"

    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE_EVIDENCE_GENERATOR),
            "--output",
            str(output),
            "--branch",
            "review/agentos-sdk-architecture-20260611",
            "--commit",
            "abc123",
            "--version",
            agentos.__version__,
            "--generated-at",
            "2026-06-17T01:00:00+08:00",
            "--independent-review-status",
            "pending",
            "--gate-result",
            "runtime_boundary_scan=2",
        ],
        cwd=ROOT,
        env=release_evidence_generator_env(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert manifest["gates"]["runtime_boundary_scan"]["status"] == "failed"
    assert manifest["gates"]["runtime_boundary_scan"]["result"] == {"exit_code": 2}
