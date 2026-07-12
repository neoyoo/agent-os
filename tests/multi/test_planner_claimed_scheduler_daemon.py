from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agentos.multi.planner import (
    InMemoryPlanStore,
    PlanClaimedSchedulerTickReport,
    PlannerClaimedSchedulerDaemon,
    PlannerClaimedSchedulerDaemonError,
    PlannerRuntime,
)


class RecordingClaimedPlannerRuntime(PlannerRuntime):
    def __init__(self) -> None:
        super().__init__(store=InMemoryPlanStore())
        self.calls: list[dict[str, object]] = []
        self.second_call = threading.Event()
        self.raise_on_call: Exception | None = None

    def claimed_scheduler_tick(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        owner_agent_id: str | None = None,
        statuses: tuple[str, ...] = ("draft", "running"),
        limit: int | None = None,
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
        release_after_tick: bool = False,
    ) -> PlanClaimedSchedulerTickReport:
        self.calls.append(
            {
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "owner_agent_id": owner_agent_id,
                "statuses": statuses,
                "limit": limit,
                "default_template_id": default_template_id,
                "retry_limit": retry_limit,
                "dispatch_limit": dispatch_limit,
                "release_after_tick": release_after_tick,
            },
        )
        if len(self.calls) >= 2:
            self.second_call.set()
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return PlanClaimedSchedulerTickReport(worker_id=worker_id)


def test_planner_claimed_scheduler_daemon_run_once_records_report() -> None:
    runtime = RecordingClaimedPlannerRuntime()
    daemon = PlannerClaimedSchedulerDaemon(
        runtime=runtime,
        worker_id="worker-1",
        lease_seconds=30.0,
        owner_agent_id="leader",
        statuses=("running",),
        limit=2,
        default_template_id="reviewer",
        retry_limit=3,
        dispatch_limit=4,
        release_after_tick=True,
        poll_interval_seconds=0.01,
    )

    report = daemon.run_once()
    state = daemon.state()

    assert report.worker_id == "worker-1"
    assert runtime.calls == [
        {
            "worker_id": "worker-1",
            "lease_seconds": 30.0,
            "owner_agent_id": "leader",
            "statuses": ("running",),
            "limit": 2,
            "default_template_id": "reviewer",
            "retry_limit": 3,
            "dispatch_limit": 4,
            "release_after_tick": True,
        },
    ]
    assert state.status == "idle"
    assert state.worker_id == "worker-1"
    assert state.owner_agent_id == "leader"
    assert state.statuses == ("running",)
    assert state.iterations == 1
    assert state.last_report is report
    assert state.errors == ()
    assert state.last_run_at is not None


def test_planner_claimed_scheduler_daemon_start_stop_and_join_polls() -> None:
    runtime = RecordingClaimedPlannerRuntime()
    daemon = PlannerClaimedSchedulerDaemon(
        runtime=runtime,
        worker_id="worker-1",
        lease_seconds=5.0,
        poll_interval_seconds=0.001,
    )

    daemon.start()
    assert runtime.second_call.wait(timeout=1.0)
    assert daemon.is_running() is True
    daemon.stop()

    assert daemon.join(timeout=1.0) is True
    state = daemon.state()
    assert state.status == "stopped"
    assert state.iterations >= 2
    assert [call["worker_id"] for call in runtime.calls[:2]] == [
        "worker-1",
        "worker-1",
    ]
    assert daemon.is_running() is False


def test_planner_claimed_scheduler_daemon_records_runtime_errors() -> None:
    runtime = RecordingClaimedPlannerRuntime()
    runtime.raise_on_call = RuntimeError("claim tick failed")
    daemon = PlannerClaimedSchedulerDaemon(
        runtime=runtime,
        worker_id="worker-1",
        lease_seconds=5.0,
    )

    with pytest.raises(RuntimeError, match="claim tick failed"):
        daemon.run_once()

    state = daemon.state()
    assert state.iterations == 1
    assert state.last_report is None
    assert state.errors == (
        PlannerClaimedSchedulerDaemonError(error="claim tick failed"),
    )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"worker_id": "", "lease_seconds": 5.0}, "worker_id"),
        ({"worker_id": "worker-1", "lease_seconds": 0.0}, "lease_seconds"),
        (
            {"worker_id": "worker-1", "lease_seconds": 5.0, "statuses": ()},
            "statuses",
        ),
        (
            {"worker_id": "worker-1", "lease_seconds": 5.0, "statuses": ("",)},
            "statuses",
        ),
        ({"worker_id": "worker-1", "lease_seconds": 5.0, "limit": 0}, "limit"),
        (
            {"worker_id": "worker-1", "lease_seconds": 5.0, "retry_limit": 0},
            "retry_limit",
        ),
        (
            {"worker_id": "worker-1", "lease_seconds": 5.0, "dispatch_limit": 0},
            "dispatch_limit",
        ),
        (
            {
                "worker_id": "worker-1",
                "lease_seconds": 5.0,
                "poll_interval_seconds": -1.0,
            },
            "poll_interval_seconds",
        ),
    ],
)
def test_planner_claimed_scheduler_daemon_rejects_invalid_configuration(
    kwargs: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        PlannerClaimedSchedulerDaemon(
            runtime=RecordingClaimedPlannerRuntime(),
            **kwargs,
        )


def test_runtime_loops_do_not_import_planner_claimed_scheduler_daemon() -> None:
    project_root = Path(__file__).resolve().parents[2]

    for path in [
        project_root / "src" / "agentos" / "runtime" / "query_loop.py",
        project_root / "src" / "agentos" / "runtime" / "async_query_loop.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "PlannerClaimedSchedulerDaemon" not in text
        assert "PlannerClaimedSchedulerDaemonState" not in text
        assert "PlannerClaimedSchedulerDaemonStatus" not in text
