from __future__ import annotations

import json
import subprocess
import sys


def _passed_run_result(backend_name: str):
    from agentos.deployment import (
        BackendVerificationRecord,
        DeploymentLiveBackendVerificationRunResult,
        LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS,
    )

    return DeploymentLiveBackendVerificationRunResult(
        command=("python", "-m", "checks.live_backend", backend_name),
        exit_code=0,
        required_backends=(backend_name,),
        records=(
            BackendVerificationRecord(
                backend_name=backend_name,
                backend_kind=LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS[
                    backend_name
                ],
                status="passed",
                checked_at=1781592000.0,
                evidence_ref=f"ci://live-backend/{backend_name}",
                target_ref=f"deployment://{backend_name}",
                metadata={"attempt": 1},
            ),
        ),
        environment="ci",
        artifact_uri=f"s3://agentos/live-backend/{backend_name}.json",
        metadata={"runner": "BackendVerificationCliRunner"},
    )


def test_reference_probe_pack_declares_state_plane_backend_probes() -> None:
    from agentos.deployment import LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    from agentos.probes import (
        REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME,
        ReferenceLiveBackendProbePack,
        ReferenceLiveBackendProbeSpec,
    )

    pack = ReferenceLiveBackendProbePack()

    assert pack.pack_name == REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME
    assert pack.required_backends == LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    assert tuple(spec.backend_name for spec in pack.probe_specs) == (
        LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    )
    assert all(isinstance(spec, ReferenceLiveBackendProbeSpec) for spec in pack.probe_specs)
    assert pack.probe_by_backend("agent_registry").backend_kind == "nacos"
    assert pack.probe_by_backend("message_queue").backend_kind == "redis"
    assert pack.probe_by_backend("task_store").backend_kind == "postgres"
    assert pack.probe_by_backend("plan_store").backend_kind == "postgres"
    assert pack.probe_by_backend("worker_process_supervisor").backend_kind == (
        "worker_process_supervisor"
    )
    assert pack.probe_by_backend("session_snapshot_persistence").backend_kind == (
        "postgres"
    )
    assert "does not create backend clients" in pack.as_dict()["sdk_owned"]
    assert (
        "credentials, migrations, CI matrix execution, alert routing and runbooks"
        in pack.as_dict()["deployment_owned"]
    )
    json.dumps(pack.as_dict())


def test_reference_probe_pack_builds_argv_only_invocation_plan() -> None:
    from agentos.deployment import BackendVerificationInvocationPlan
    from agentos.probes import ReferenceLiveBackendProbePack

    pack = ReferenceLiveBackendProbePack(
        command_prefix=("python", "-m", "checks.live_backend"),
        artifact_uri_prefix="s3://agentos/live-backend",
        environment="ci",
    )

    plan = pack.invocation_plan("agent_registry")

    assert isinstance(plan, BackendVerificationInvocationPlan)
    assert plan.command == (
        "python",
        "-m",
        "checks.live_backend",
        "agent_registry",
    )
    assert plan.required_backends == ("agent_registry",)
    assert plan.metadata["probe_pack"] == "reference_live_backend_probe_pack"
    assert plan.metadata["backend_kind"] == "nacos"
    assert plan.metadata["adapter_hint"] == "NacosAgentRegistryAdapter"
    assert plan.metadata["deployment_stage"] == "ci"
    assert plan.metadata["artifact_uri"] == (
        "s3://agentos/live-backend/agent_registry.json"
    )
    json.dumps(plan.as_dict())


def test_reference_probe_pack_default_invocation_uses_sdk_example_module() -> None:
    from agentos.probes import ReferenceLiveBackendProbePack

    plan = ReferenceLiveBackendProbePack().invocation_plan("agent_registry")

    assert plan.command == (
        "python",
        "-m",
        "agentos.examples.live_backend_probe",
        "agent_registry",
    )


def test_reference_live_backend_probe_example_emits_importable_stdout_json() -> None:
    from agentos.deployment import BackendVerificationReportImporter

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentos.examples.live_backend_probe",
            "agent_registry",
            "--status",
            "passed",
            "--checked-at",
            "1781592000",
            "--evidence-ref",
            "ci://live-backend/agent_registry",
            "--target-ref",
            "nacos://agentos/agent-registry",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    records = BackendVerificationReportImporter().from_json(completed.stdout)

    assert len(records) == 1
    assert records[0].backend_name == "agent_registry"
    assert records[0].backend_kind == "nacos"
    assert records[0].status == "skipped"
    assert records[0].target_ref == "nacos://agentos/agent-registry"


def test_reference_live_backend_probe_example_does_not_certify_passed_status() -> None:
    from agentos.deployment import BackendVerificationReportImporter

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentos.examples.live_backend_probe",
            "agent_registry",
            "--status",
            "passed",
            "--checked-at",
            "1781592000",
            "--evidence-ref",
            "ci://live-backend/agent_registry",
            "--target-ref",
            "nacos://agentos/agent-registry",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    records = BackendVerificationReportImporter().from_json(completed.stdout)
    payload = json.loads(completed.stdout)

    assert records[0].status == "skipped"
    assert records[0].error == "reference probe cannot certify a live backend check"
    assert payload["records"][0]["metadata"]["requested_status"] == "passed"
    assert payload["records"][0]["metadata"]["certification_claim"] == (
        "non-certifying-example"
    )


def test_reference_live_backend_probe_example_defaults_to_non_certifying_unknown() -> None:
    from agentos.deployment import BackendVerificationReportImporter

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentos.examples.live_backend_probe",
            "agent_registry",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    records = BackendVerificationReportImporter().from_json(completed.stdout)
    payload = json.loads(completed.stdout)

    assert len(records) == 1
    assert records[0].backend_name == "agent_registry"
    assert records[0].status == "unknown"
    assert records[0].error == "reference probe did not execute a live backend check"
    assert payload["records"][0]["metadata"]["does not create backend clients"] is True
    assert payload["records"][0]["metadata"]["certification_claim"] == (
        "non-certifying-example"
    )


def test_reference_probe_pack_rejects_shell_strings_and_secret_metadata() -> None:
    import pytest

    from agentos.probes import ReferenceLiveBackendProbePack, ReferenceLiveBackendProbeSpec

    with pytest.raises(ValueError, match="command_prefix"):
        ReferenceLiveBackendProbePack(command_prefix=())

    with pytest.raises(ValueError, match="command_prefix"):
        ReferenceLiveBackendProbePack(command_prefix=("python -m checks.live_backend",))

    with pytest.raises(ValueError, match="metadata contains restricted key"):
        ReferenceLiveBackendProbeSpec(
            backend_name="agent_registry",
            backend_kind="nacos",
            adapter_hint="NacosAgentRegistryAdapter",
            metadata={"token": "secret"},
        )

    with pytest.raises(KeyError, match="unknown backend probe"):
        ReferenceLiveBackendProbePack().probe_by_backend("missing_backend")


def test_reference_probe_pack_builds_readiness_bundle_from_run_results() -> None:
    from agentos.readiness import ProductionReadinessEvidenceBundle
    from agentos.probes import ReferenceLiveBackendProbePack

    pack = ReferenceLiveBackendProbePack()
    results = {
        backend_name: _passed_run_result(backend_name)
        for backend_name in pack.required_backends
    }

    bundle = pack.readiness_bundle(results)
    payload = bundle.as_dict()

    assert isinstance(bundle, ProductionReadinessEvidenceBundle)
    assert bundle.bundle_name == "reference_live_backend_probe_pack"
    assert bundle.accepted is True
    assert bundle.block_production_readiness is False
    assert bundle.required_checks == pack.required_backends
    assert payload["metadata"]["probe_pack"] == "reference_live_backend_probe_pack"
    assert payload["metadata"]["readiness source aggregation"] is True
    assert payload["metadata"]["does not create backend clients"] is True
    assert all(check["status"] == "passed" for check in payload["checks"])
    json.dumps(payload)


def test_backend_verification_run_result_redacts_secret_patterns_in_output() -> None:
    from agentos.deployment import DeploymentLiveBackendVerificationRunResult

    result = DeploymentLiveBackendVerificationRunResult(
        command=("python", "-m", "checks.live_backend", "--api-key", "sk-command"),
        exit_code=1,
        required_backends=("agent_registry",),
        stdout_summary=(
            "Authorization: Bearer raw-bearer\n"
            "dsn=postgres://user:secret@db/agentos\n"
        ),
        stderr_summary="failed with sk-live-secret",
        metadata={"log": "token=raw-token"},
    )

    payload_text = json.dumps(result.as_dict())

    assert "raw-bearer" not in payload_text
    assert "user:secret" not in payload_text
    assert "sk-live-secret" not in payload_text
    assert "raw-token" not in payload_text
    assert "sk-command" not in payload_text
    assert "<redacted>" in payload_text


def test_reference_probe_pack_readiness_bundle_blocks_missing_backend_result() -> None:
    from agentos.probes import ReferenceLiveBackendProbePack

    pack = ReferenceLiveBackendProbePack()
    results = {
        "agent_registry": _passed_run_result("agent_registry"),
    }

    bundle = pack.readiness_bundle(results)

    assert bundle.accepted is False
    assert bundle.block_production_readiness is True
    assert bundle.missing_required_checks == (
        "message_queue",
        "task_store",
        "plan_store",
        "worker_process_supervisor",
        "session_snapshot_persistence",
    )
