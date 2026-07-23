from __future__ import annotations

import json

import pytest

from agentos.deployment_constants import (
    LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS,
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
)
from agentos.deployment_profiles import DeploymentLiveBackendVerificationProfile
from agentos.deployment_reports import DeploymentLiveBackendVerificationGateReport
from agentos.deployment_types import BackendVerificationRecord


def _passed_record(name: str) -> BackendVerificationRecord:
    return BackendVerificationRecord(
        backend_name=name,
        backend_kind=LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS[name],
        status="passed",
        checked_at=1781580000.0,
        evidence_ref=f"ci://live-backend/{name}",
        target_ref=f"deployment://{name}",
        metadata={"attempt": 1},
    )


def test_backend_verification_record_payload_is_json_safe() -> None:
    record = BackendVerificationRecord(
        backend_name="postgres_state_store",
        backend_kind="postgres",
        status="passed",
        checked_at=1781580000.0,
        evidence_ref="ci://checks/postgres-state-store",
        target_ref="postgresql://agentos/state",
        metadata={"namespace": "agentos-prod", "attempt": 2},
    )

    payload = record.as_dict()

    assert payload == {
        "backend_name": "postgres_state_store",
        "backend_kind": "postgres",
        "status": "passed",
        "checked_at": 1781580000.0,
        "evidence_ref": "ci://checks/postgres-state-store",
        "target_ref": "postgresql://agentos/state",
        "error": None,
        "metadata": {"namespace": "agentos-prod", "attempt": 2},
    }
    json.dumps(payload)


def test_backend_verification_record_redacts_secret_patterns_in_target_ref() -> None:
    record = BackendVerificationRecord(
        backend_name="postgres_artifact_store",
        backend_kind="postgres",
        status="passed",
        checked_at=1781580000.0,
        evidence_ref="ci://checks/postgres-session-snapshots",
        target_ref="postgresql://agentos:raw-password@db.example/agentos",
        metadata={"note": "safe"},
    )

    payload = record.as_dict()

    assert payload["target_ref"] == "postgresql://<redacted>@db.example/agentos"
    assert "raw-password" not in json.dumps(payload)


def test_backend_verification_record_redacts_secret_patterns_in_evidence_and_error() -> None:
    record = BackendVerificationRecord(
        backend_name="redis_worker_queue",
        backend_kind="redis",
        status="failed",
        checked_at=1781580000.0,
        evidence_ref="https://ci.example/run?api_key=raw-token",
        error="Authorization: Bearer raw-token failed for redis://:raw-password@redis/0",
    )

    payload = record.as_dict()
    rendered = json.dumps(payload)

    assert payload["evidence_ref"] == "https://ci.example/run?api_key=<redacted>"
    assert payload["error"] == (
        "Authorization: Bearer <redacted> failed for redis://<redacted>@redis/0"
    )
    assert "raw-token" not in rendered
    assert "raw-password" not in rendered


def test_backend_verification_record_rejects_missing_refs_and_secret_metadata() -> None:
    with pytest.raises(ValueError, match="backend_name must not be empty"):
        BackendVerificationRecord(
            backend_name=" ",
            backend_kind="postgres",
            status="passed",
            checked_at=1.0,
            evidence_ref="ci://check",
        )

    with pytest.raises(ValueError, match="evidence_ref must not be empty"):
        BackendVerificationRecord(
            backend_name="postgres_state_store",
            backend_kind="postgres",
            status="passed",
            checked_at=1.0,
            evidence_ref=" ",
        )

    with pytest.raises(ValueError, match="metadata contains restricted key"):
        BackendVerificationRecord(
            backend_name="redis_worker_queue",
            backend_kind="redis",
            status="passed",
            checked_at=1.0,
            evidence_ref="ci://check",
            metadata={"connection_string": "redis://secret@example"},
        )


def test_live_backend_verification_gate_accepts_all_required_passed_records() -> None:
    records = tuple(
        _passed_record(name)
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        records,
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is True
    assert report.block_production_readiness is False
    assert report.missing_backends == ()
    assert report.failed_backends == ()
    assert report.skipped_backends == ()
    assert report.unknown_backends == ()
    assert [record.backend_name for record in report.records] == list(
        LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )
    json.dumps(report.as_dict())


def test_live_backend_verification_gate_blocks_missing_backend_evidence() -> None:
    records = (
        _passed_record("postgres_state_store"),
        _passed_record("postgres_artifact_store"),
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        records,
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.block_production_readiness is True
    assert report.missing_backends == (
        "redis_worker_queue",
        "redis_relay_queue",
        "redis_event_replay",
        "distributed_worker",
    )


def test_live_backend_verification_gate_blocks_failed_or_unknown_backend() -> None:
    records = [
        _passed_record(name)
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    ]
    records[1] = BackendVerificationRecord(
        backend_name="postgres_artifact_store",
        backend_kind="postgres",
        status="failed",
        checked_at=1781580001.0,
        evidence_ref="ci://checks/postgres-artifact-store",
        error="ping timeout",
    )
    records[3] = BackendVerificationRecord(
        backend_name="redis_relay_queue",
        backend_kind="redis",
        status="unknown",
        checked_at=1781580002.0,
        evidence_ref="ci://checks/redis-relay-queue",
        error="report missing assertion",
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        records,
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.block_production_readiness is True
    assert report.failed_backends == ("postgres_artifact_store",)
    assert report.unknown_backends == ("redis_relay_queue",)
    assert report.as_dict()["status"] == "failed"


def test_live_backend_verification_gate_blocks_passed_record_without_target_ref() -> None:
    records = [
        _passed_record(name)
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    ]
    records[0] = BackendVerificationRecord(
        backend_name="postgres_state_store",
        backend_kind="postgres",
        status="passed",
        checked_at=1781580000.0,
        evidence_ref="ci://checks/postgres-state-store",
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        tuple(records),
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.block_production_readiness is True
    assert report.invalid_backends == ("postgres_state_store",)
    assert report.as_dict()["invalid_backends"] == ("postgres_state_store",)


def test_live_backend_verification_gate_blocks_backend_kind_mismatch() -> None:
    records = [
        _passed_record(name)
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    ]
    records[2] = BackendVerificationRecord(
        backend_name="redis_worker_queue",
        backend_kind="postgres",
        status="passed",
        checked_at=1781580000.0,
        evidence_ref="ci://checks/redis-worker-queue",
        target_ref="redis://deployment.example/0",
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        tuple(records),
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.invalid_backends == ("redis_worker_queue",)


def test_live_backend_verification_gate_blocks_passed_record_without_checked_at() -> None:
    records = [
        _passed_record(name)
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    ]
    records[1] = BackendVerificationRecord(
        backend_name="postgres_artifact_store",
        backend_kind="postgres",
        status="passed",
        checked_at=0.0,
        evidence_ref="ci://checks/postgres-artifact-store",
        target_ref="postgresql://deployment.example/agentos",
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        tuple(records),
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.invalid_backends == ("postgres_artifact_store",)


def test_live_backend_verification_gate_blocks_placeholder_evidence_ref() -> None:
    records = [
        _passed_record(name)
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    ]
    records[2] = BackendVerificationRecord(
        backend_name="redis_worker_queue",
        backend_kind="redis",
        status="passed",
        checked_at=1781580000.0,
        evidence_ref="<artifact-or-log-ref>",
        target_ref="redis://deployment.example/0",
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        tuple(records),
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.invalid_backends == ("redis_worker_queue",)


def test_live_backend_verification_gate_blocks_duplicate_backend_records() -> None:
    records = tuple(
        _passed_record(name)
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    ) + (_passed_record("redis_relay_queue"),)

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        records,
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.duplicate_backends == ("redis_relay_queue",)
    assert report.as_dict()["duplicate_backends"] == ("redis_relay_queue",)


def test_live_backend_verification_profile_exposes_readiness_check() -> None:
    profile = DeploymentLiveBackendVerificationProfile(
        records=tuple(
            _passed_record(name)
            for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
        ),
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    metadata = profile.readiness_metadata()
    check = profile.readiness_check()

    assert metadata["profile"] == "DeploymentLiveBackendVerificationProfile"
    assert metadata["probe_name"] == "deployment_live_backend_verification"
    assert metadata["ready"] is True
    assert metadata["block_production_readiness"] is False
    assert "backend check execution" in metadata["deployment_owned"]
    assert "BackendVerificationRecord" in metadata["sdk_owned"]
    assert check["ok"] is True
    assert check["status"] == "ok"
