from __future__ import annotations

import json

import pytest

from agentos.deployment import (
    BackendVerificationRecord,
    DeploymentLiveBackendVerificationGateReport,
    DeploymentLiveBackendVerificationProfile,
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
)


def _passed_record(name: str) -> BackendVerificationRecord:
    return BackendVerificationRecord(
        backend_name=name,
        backend_kind=name,
        status="passed",
        checked_at=1781580000.0,
        evidence_ref=f"ci://live-backend/{name}",
        target_ref=f"deployment://{name}",
        metadata={"attempt": 1},
    )


def test_backend_verification_record_payload_is_json_safe() -> None:
    record = BackendVerificationRecord(
        backend_name="agent_registry",
        backend_kind="nacos",
        status="passed",
        checked_at=1781580000.0,
        evidence_ref="ci://checks/nacos-agent-registry",
        target_ref="nacos://agentos/agent-registry",
        metadata={"namespace": "agentos-prod", "attempt": 2},
    )

    payload = record.as_dict()

    assert payload == {
        "backend_name": "agent_registry",
        "backend_kind": "nacos",
        "status": "passed",
        "checked_at": 1781580000.0,
        "evidence_ref": "ci://checks/nacos-agent-registry",
        "target_ref": "nacos://agentos/agent-registry",
        "error": None,
        "metadata": {"namespace": "agentos-prod", "attempt": 2},
    }
    json.dumps(payload)


def test_backend_verification_record_rejects_missing_refs_and_secret_metadata() -> None:
    with pytest.raises(ValueError, match="backend_name must not be empty"):
        BackendVerificationRecord(
            backend_name=" ",
            backend_kind="nacos",
            status="passed",
            checked_at=1.0,
            evidence_ref="ci://check",
        )

    with pytest.raises(ValueError, match="evidence_ref must not be empty"):
        BackendVerificationRecord(
            backend_name="agent_registry",
            backend_kind="nacos",
            status="passed",
            checked_at=1.0,
            evidence_ref=" ",
        )

    with pytest.raises(ValueError, match="metadata contains restricted key"):
        BackendVerificationRecord(
            backend_name="message_queue",
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
        _passed_record("agent_registry"),
        _passed_record("message_queue"),
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        records,
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.block_production_readiness is True
    assert report.missing_backends == (
        "task_store",
        "plan_store",
        "worker_process_supervisor",
        "session_snapshot_persistence",
    )


def test_live_backend_verification_gate_blocks_failed_or_unknown_backend() -> None:
    records = [
        _passed_record(name)
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    ]
    records[1] = BackendVerificationRecord(
        backend_name="message_queue",
        backend_kind="redis",
        status="failed",
        checked_at=1781580001.0,
        evidence_ref="ci://checks/redis-message-queue",
        error="ping timeout",
    )
    records[3] = BackendVerificationRecord(
        backend_name="plan_store",
        backend_kind="postgres",
        status="unknown",
        checked_at=1781580002.0,
        evidence_ref="ci://checks/postgres-plan-store",
        error="report missing assertion",
    )

    report = DeploymentLiveBackendVerificationGateReport.from_records(
        records,
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    assert report.accepted is False
    assert report.block_production_readiness is True
    assert report.failed_backends == ("message_queue",)
    assert report.unknown_backends == ("plan_store",)
    assert report.as_dict()["status"] == "failed"


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
