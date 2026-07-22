from __future__ import annotations

import json
import sys

import pytest

from agentos.deployment_types import WorkerProcessSpec, WorkerProcessState
from agentos.deployment_workers import LocalSubprocessWorkerSupervisor


def test_worker_process_spec_rejects_empty_identity_and_command() -> None:
    with pytest.raises(ValueError, match="worker_id must not be empty"):
        WorkerProcessSpec(worker_id=" ", command=(sys.executable,))

    with pytest.raises(ValueError, match="command must not be empty"):
        WorkerProcessSpec(worker_id="worker-1", command=())

    with pytest.raises(ValueError, match="command must not contain empty values"):
        WorkerProcessSpec(worker_id="worker-1", command=(sys.executable, ""))

    with pytest.raises(ValueError, match="env must not contain empty names"):
        WorkerProcessSpec(
            worker_id="worker-1",
            command=(sys.executable, "-c", "print('ok')"),
            env={" ": "secret"},
        )

    with pytest.raises(ValueError, match="metadata contains restricted key"):
        WorkerProcessSpec(
            worker_id="worker-1",
            command=(sys.executable, "-c", "print('ok')"),
            metadata={"secret_token": "not-evidence"},
        )


def test_worker_process_state_evidence_is_json_safe_and_redacts_env_values() -> None:
    spec = WorkerProcessSpec(
        worker_id="team-worker-1",
        command=(sys.executable, "-c", "print('hello')"),
        worker_kind="team_worker",
        env={"AGENTOS_SECRET": "not-for-evidence"},
        metadata={"team_id": "team-1"},
    )
    state = WorkerProcessState.from_spec(
        spec,
        status="running",
        pid=1234,
        started_at=10.5,
    )

    evidence = state.to_evidence()

    assert evidence["worker_id"] == "team-worker-1"
    assert evidence["worker_kind"] == "team_worker"
    assert evidence["status"] == "running"
    assert evidence["pid"] == 1234
    assert evidence["command"] == (sys.executable, "-c", "print('hello')")
    assert evidence["env_keys"] == ("AGENTOS_SECRET",)
    assert "not-for-evidence" not in json.dumps(evidence)
    json.dumps(evidence)


def test_worker_process_state_evidence_records_heartbeat_timestamp() -> None:
    spec = WorkerProcessSpec(
        worker_id="planner-worker-heartbeat",
        command=(sys.executable, "-c", "print('heartbeat')"),
        worker_kind="planner_worker",
    )
    state = WorkerProcessState.from_spec(
        spec,
        status="running",
        pid=1234,
        started_at=10.0,
        last_heartbeat_at=12.5,
    )

    evidence = state.to_evidence()

    assert evidence["last_heartbeat_at"] == 12.5
    json.dumps(evidence)


def test_worker_process_state_rejects_secret_like_metadata_when_directly_constructed() -> None:
    with pytest.raises(ValueError, match="metadata contains restricted key"):
        WorkerProcessState(
            worker_id="planner-worker-secret",
            worker_kind="planner_worker",
            command=(sys.executable,),
            status="running",
            metadata={"nested": {"api_token": "raw-token"}},
        )


def test_worker_process_state_evidence_redacts_secret_command_arguments() -> None:
    spec = WorkerProcessSpec(
        worker_id="team-worker-1",
        command=(
            sys.executable,
            "-m",
            "agent_worker",
            "--token",
            "raw-token",
            "--api-key=raw-api-key",
        ),
        worker_kind="team_worker",
    )
    state = WorkerProcessState.from_spec(spec, status="configured")

    evidence = state.to_evidence()

    assert state.command == spec.command
    assert evidence["command"] == (
        sys.executable,
        "-m",
        "agent_worker",
        "--token",
        "<redacted>",
        "--api-key=<redacted>",
    )
    encoded = json.dumps(evidence)
    assert "raw-token" not in encoded
    assert "raw-api-key" not in encoded


def test_local_subprocess_worker_supervisor_records_exit_evidence() -> None:
    supervisor = LocalSubprocessWorkerSupervisor()
    spec = WorkerProcessSpec(
        worker_id="planner-worker-1",
        command=(sys.executable, "-c", "import sys; sys.exit(3)"),
        worker_kind="planner_worker",
    )

    started = supervisor.start(spec)
    finished = supervisor.wait("planner-worker-1", timeout_seconds=5)

    assert started.status == "running"
    assert started.pid is not None
    assert finished.status == "failed"
    assert finished.exit_code == 3
    assert finished.started_at is not None
    assert finished.stopped_at is not None
    assert supervisor.state("planner-worker-1") == finished
    assert supervisor.evidence("planner-worker-1")["status"] == "failed"


def test_local_subprocess_worker_supervisor_records_heartbeat_evidence() -> None:
    supervisor = LocalSubprocessWorkerSupervisor(
        clock=iter([10.0, 12.5, 13.0, 14.0]).__next__,
    )
    spec = WorkerProcessSpec(
        worker_id="planner-worker-heartbeat",
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        worker_kind="planner_worker",
    )

    supervisor.start(spec)
    try:
        heartbeat = supervisor.heartbeat("planner-worker-heartbeat")
    finally:
        supervisor.stop("planner-worker-heartbeat", timeout_seconds=5)

    assert heartbeat.status == "running"
    assert heartbeat.last_heartbeat_at == 12.5
    assert supervisor.evidence("planner-worker-heartbeat")[
        "last_heartbeat_at"
    ] == 12.5


def test_local_subprocess_worker_supervisor_does_not_inherit_host_environment(
    tmp_path,
    monkeypatch,
) -> None:
    output_path = tmp_path / "env.txt"
    monkeypatch.setenv("AGENTOS_HOST_SECRET", "parent-secret")
    supervisor = LocalSubprocessWorkerSupervisor()
    spec = WorkerProcessSpec(
        worker_id="env-isolated-worker",
        command=(
            sys.executable,
            "-c",
            (
                "import os, pathlib; "
                f"pathlib.Path({str(output_path)!r}).write_text("
                "os.environ.get('AGENTOS_HOST_SECRET', '<missing>') + '\\n' + "
                "os.environ.get('AGENTOS_CHILD_VALUE', '<missing>'), "
                "encoding='utf-8')"
            ),
        ),
        env={"AGENTOS_CHILD_VALUE": "child-only"},
    )

    supervisor.start(spec)
    finished = supervisor.wait("env-isolated-worker", timeout_seconds=5)

    assert finished.status == "exited"
    assert output_path.read_text(encoding="utf-8") == "<missing>\nchild-only"


def test_local_subprocess_worker_supervisor_stops_running_process() -> None:
    supervisor = LocalSubprocessWorkerSupervisor()
    spec = WorkerProcessSpec(
        worker_id="a2a-push-worker-1",
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        worker_kind="a2a_push_worker",
    )

    started = supervisor.start(spec)
    stopped = supervisor.stop("a2a-push-worker-1", timeout_seconds=5)

    assert started.status == "running"
    assert stopped.status == "stopped"
    assert stopped.stop_requested_at is not None
    assert stopped.stopped_at is not None
    assert stopped.exit_code is not None
    assert supervisor.is_running("a2a-push-worker-1") is False


def test_local_subprocess_worker_supervisor_rejects_duplicate_running_worker() -> None:
    supervisor = LocalSubprocessWorkerSupervisor()
    spec = WorkerProcessSpec(
        worker_id="team-worker-duplicate",
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
    )

    supervisor.start(spec)
    try:
        with pytest.raises(ValueError, match="worker is already running"):
            supervisor.start(spec)
    finally:
        supervisor.stop("team-worker-duplicate", timeout_seconds=5)
