from __future__ import annotations

import json
import sys


def _passed_record_payload(name: str) -> dict[str, object]:
    return {
        "backend_name": name,
        "backend_kind": name,
        "status": "passed",
        "checked_at": 1781589000.0,
        "evidence_ref": f"ci://live-backend/{name}",
        "target_ref": f"deployment://{name}",
        "metadata": {"attempt": 1},
    }


def test_backend_verification_report_importer_parses_canonical_json() -> None:
    from agentos.deployment import BackendVerificationReportImporter

    payload = {
        "records": [
            {
                "backendName": "agent_registry",
                "backendKind": "nacos",
                "status": "ok",
                "checkedAt": 1781589000.0,
                "evidenceRef": "ci://checks/nacos-agent-registry",
                "targetRef": "nacos://agentos/agent-registry",
                "metadata": {"namespace": "agentos-prod"},
            },
            {
                "backend_name": "message_queue",
                "backend_kind": "redis",
                "status": "failed",
                "checked_at": 1781589001.0,
                "evidence_ref": "ci://checks/redis-message-queue",
                "error": "ping timeout",
            },
        ],
    }

    records = BackendVerificationReportImporter().from_json(json.dumps(payload))

    assert [record.backend_name for record in records] == [
        "agent_registry",
        "message_queue",
    ]
    assert records[0].backend_kind == "nacos"
    assert records[0].status == "passed"
    assert records[0].target_ref == "nacos://agentos/agent-registry"
    assert records[1].status == "failed"
    assert records[1].error == "ping timeout"
    json.dumps([record.as_dict() for record in records])


def test_backend_verification_cli_runner_imports_report_path(tmp_path) -> None:
    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
        DeploymentLiveBackendVerificationRunResult,
        LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    report_path = tmp_path / "live-backend-report.json"
    report_payload = {
        "records": [
            _passed_record_payload(name)
            for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
        ],
    }
    script = (
        "import json\n"
        "from pathlib import Path\n"
        f"Path({str(report_path)!r}).write_text("
        f"{json.dumps(report_payload)!r}, encoding='utf-8')\n"
        "print('backend verification finished')\n"
    )
    plan = BackendVerificationInvocationPlan(
        command=(sys.executable, "-c", script),
        required_backends=LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )

    result = BackendVerificationCliRunner(
        timeout_seconds=10,
        report_path=report_path,
        environment="ci",
        artifact_uri="s3://ci/live-backend/report.json",
    ).run(plan)

    assert isinstance(result, DeploymentLiveBackendVerificationRunResult)
    assert result.exit_code == 0
    assert result.execution_succeeded is True
    assert result.accepted is True
    assert result.block_production_readiness is False
    assert result.stdout_summary == "backend verification finished\n"
    assert result.stderr_summary == ""
    assert result.environment == "ci"
    assert result.artifact_uri == "s3://ci/live-backend/report.json"
    assert [record.backend_name for record in result.records] == list(
        LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )
    assert result.metadata["runner"] == "BackendVerificationCliRunner"
    assert result.metadata["timeout_seconds"] == 10
    assert result.metadata["report_source"] == str(report_path)
    assert result.gate_report().accepted is True
    json.dumps(result.as_dict())


def test_backend_verification_cli_runner_imports_stdout_json() -> None:
    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
    )

    report_payload = {
        "records": [
            _passed_record_payload("agent_registry"),
        ],
    }
    plan = BackendVerificationInvocationPlan(
        command=(sys.executable, "-c", f"print({json.dumps(report_payload)!r})"),
        required_backends=("agent_registry",),
    )

    result = BackendVerificationCliRunner().run(plan)

    assert result.exit_code == 0
    assert result.accepted is True
    assert result.records[0].backend_name == "agent_registry"
    assert result.metadata["report_source"] == "stdout"


def test_backend_verification_cli_runner_records_timeout_as_blocking_evidence() -> None:
    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
    )

    plan = BackendVerificationInvocationPlan(
        command=(
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(2)",
        ),
        required_backends=("agent_registry",),
    )

    result = BackendVerificationCliRunner(timeout_seconds=0.01).run(plan)

    assert result.exit_code == 124
    assert result.execution_succeeded is False
    assert result.accepted is False
    assert result.block_production_readiness is True
    assert result.records == ()
    assert result.gate_report().missing_backends == ("agent_registry",)
    assert result.metadata["timed_out"] is True
    assert "timed out" in result.stderr_summary


def test_backend_verification_cli_runner_records_nonzero_exit_and_env_keys() -> None:
    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
    )

    plan = BackendVerificationInvocationPlan(
        command=(
            sys.executable,
            "-c",
            "import os, sys; print(os.environ['BACKEND_TOKEN']); "
            "print('broken', file=sys.stderr); sys.exit(7)",
        ),
        required_backends=("agent_registry",),
    )

    result = BackendVerificationCliRunner(
        env={"BACKEND_TOKEN": "secret-token"},
    ).run(plan)

    assert result.exit_code == 7
    assert result.execution_succeeded is False
    assert result.accepted is False
    assert result.block_production_readiness is True
    assert result.stdout_summary == "[redacted]\n"
    assert result.stderr_summary == "broken\n"
    assert result.metadata["env_keys"] == ("BACKEND_TOKEN",)
    assert "secret-token" not in repr(result.metadata)
    assert "secret-token" not in result.stdout_summary


def test_backend_verification_evidence_redacts_secret_command_arguments() -> None:
    from agentos.deployment import (
        BackendVerificationInvocationPlan,
        DeploymentLiveBackendVerificationRunResult,
    )

    plan = BackendVerificationInvocationPlan(
        command=(
            "backend-check",
            "--token",
            "raw-token",
            "--api-key=raw-api-key",
            "--header",
            "Authorization: Bearer raw-bearer",
        ),
        required_backends=("agent_registry",),
    )
    result = DeploymentLiveBackendVerificationRunResult(
        command=plan.command,
        exit_code=0,
        required_backends=("agent_registry",),
    )

    assert result.command == plan.command
    assert plan.as_dict()["command"] == (
        "backend-check",
        "--token",
        "<redacted>",
        "--api-key=<redacted>",
        "--header",
        "Authorization: Bearer <redacted>",
    )
    encoded = json.dumps(
        {
            "plan": plan.as_dict(),
            "result": result.as_dict(),
        },
    )
    assert "raw-token" not in encoded
    assert "raw-api-key" not in encoded
    assert "raw-bearer" not in encoded


def test_backend_verification_cli_runner_does_not_inherit_host_environment(
    monkeypatch,
) -> None:
    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
    )

    monkeypatch.setenv("AGENTOS_BACKEND_HOST_SECRET", "parent-secret")
    plan = BackendVerificationInvocationPlan(
        command=(
            sys.executable,
            "-c",
            "import os; print(os.environ.get('AGENTOS_BACKEND_HOST_SECRET', '<missing>'))",
        ),
        required_backends=("agent_registry",),
    )

    result = BackendVerificationCliRunner().run(plan)

    assert result.exit_code == 0
    assert result.stdout_summary == "<missing>\n"


def test_backend_verification_cli_runner_env_is_explicit_allowlist(
    monkeypatch,
) -> None:
    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
    )

    monkeypatch.setenv("AGENTOS_BACKEND_HOST_SECRET", "parent-secret")
    plan = BackendVerificationInvocationPlan(
        command=(
            sys.executable,
            "-c",
            "import os; "
            "print(os.environ.get('BACKEND_TOKEN', '<missing>')); "
            "print(os.environ.get('AGENTOS_BACKEND_HOST_SECRET', '<missing>'))",
        ),
        required_backends=("agent_registry",),
    )

    result = BackendVerificationCliRunner(
        env={"BACKEND_TOKEN": "secret-token"},
    ).run(plan)

    assert result.exit_code == 0
    assert result.stdout_summary == "[redacted]\n<missing>\n"
    assert result.metadata["env_keys"] == ("BACKEND_TOKEN",)


def test_backend_verification_cli_runner_records_report_import_error(tmp_path) -> None:
    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
    )

    report_path = tmp_path / "malformed-report.json"
    report_path.write_text("{not-json", encoding="utf-8")
    plan = BackendVerificationInvocationPlan(
        command=(sys.executable, "-c", "print('wrote malformed report')"),
        required_backends=("agent_registry",),
    )

    result = BackendVerificationCliRunner(report_path=report_path).run(plan)

    assert result.exit_code == 0
    assert result.records == ()
    assert result.accepted is False
    assert result.block_production_readiness is True
    assert result.metadata["report_source"] == str(report_path)
    assert "report_import_error" in result.metadata


def test_backend_verification_cli_runner_bounds_output_summaries() -> None:
    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
    )

    plan = BackendVerificationInvocationPlan(
        command=(
            sys.executable,
            "-c",
            "import sys; print('abcdef'); print('uvwxyz', file=sys.stderr)",
        ),
        required_backends=("agent_registry",),
    )

    result = BackendVerificationCliRunner(
        stdout_limit=3,
        stderr_limit=4,
    ).run(plan)

    assert result.stdout_summary == "abc"
    assert result.stderr_summary == "uvwx"


def test_backend_verification_cli_runner_rejects_invalid_settings() -> None:
    import pytest

    from agentos.deployment import (
        BackendVerificationCliRunner,
        BackendVerificationInvocationPlan,
    )

    with pytest.raises(ValueError, match="command"):
        BackendVerificationInvocationPlan(command=())

    with pytest.raises(ValueError, match="required_backends"):
        BackendVerificationInvocationPlan(
            command=("backend-check",),
            required_backends=("agent_registry", " "),
        )

    with pytest.raises(ValueError, match="timeout_seconds"):
        BackendVerificationCliRunner(timeout_seconds=0)

    with pytest.raises(ValueError, match="stdout_limit"):
        BackendVerificationCliRunner(stdout_limit=-1)

    with pytest.raises(ValueError, match="stderr_limit"):
        BackendVerificationCliRunner(stderr_limit=-1)

    with pytest.raises(ValueError, match="environment"):
        BackendVerificationCliRunner(environment=" ")

    with pytest.raises(ValueError, match="artifact_uri"):
        BackendVerificationCliRunner(artifact_uri=" ")

    with pytest.raises(ValueError, match="env"):
        BackendVerificationCliRunner(env={" ": "secret"})


def test_backend_verification_run_result_rejects_secret_metadata() -> None:
    import pytest

    from agentos.deployment import DeploymentLiveBackendVerificationRunResult

    with pytest.raises(ValueError, match="metadata contains restricted key"):
        DeploymentLiveBackendVerificationRunResult(
            command=("backend-check",),
            exit_code=0,
            required_backends=("agent_registry",),
            metadata={"token": "secret-token"},
        )
