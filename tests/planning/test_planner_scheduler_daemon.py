from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentos.planning import (
    InMemoryPlanStore,
    PlannerRuntime,
    PlannerSchedulerDaemon,
    PlannerSchedulerDaemonError,
)
from agentos.planning.scheduling_reports import PlanSchedulerTickReport
from tests.planning._async import async_test


class RecordingPlannerRuntime(PlannerRuntime):
    def __init__(self) -> None:
        super().__init__(store=InMemoryPlanStore())
        self.calls: list[dict[str, object]] = []
        self.second_call = asyncio.Event()
        self.fail_plan_ids: set[str] = set()

    async def scheduler_tick(
        self,
        plan_id: str,
        *,
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
    ) -> PlanSchedulerTickReport:
        self.calls.append(
            {
                "plan_id": plan_id,
                "default_template_id": default_template_id,
                "retry_limit": retry_limit,
                "dispatch_limit": dispatch_limit,
            },
        )
        if len(self.calls) >= 2:
            self.second_call.set()
        if plan_id in self.fail_plan_ids:
            raise RuntimeError(f"{plan_id} failed")
        return PlanSchedulerTickReport(plan_id=plan_id)


@async_test
async def test_planner_scheduler_daemon_run_once_records_reports_and_errors() -> None:
    runtime = RecordingPlannerRuntime()
    runtime.fail_plan_ids.add("plan_error")
    daemon = PlannerSchedulerDaemon(
        runtime=runtime,
        plan_ids=("plan_ok", "plan_error"),
        default_template_id="reviewer",
        retry_limit=2,
        dispatch_limit=3,
        poll_interval_seconds=0.01,
    )

    reports = await daemon.run_once()
    state = daemon.state()

    assert [report.plan_id for report in reports] == ["plan_ok"]
    assert runtime.calls == [
        {
            "plan_id": "plan_ok",
            "default_template_id": "reviewer",
            "retry_limit": 2,
            "dispatch_limit": 3,
        },
        {
            "plan_id": "plan_error",
            "default_template_id": "reviewer",
            "retry_limit": 2,
            "dispatch_limit": 3,
        },
    ]
    assert state.status == "idle"
    assert state.plan_ids == ("plan_ok", "plan_error")
    assert state.iterations == 1
    assert state.last_reports == reports
    assert state.errors == (
        PlannerSchedulerDaemonError(
            plan_id="plan_error",
            error="plan_error failed",
        ),
    )
    assert state.last_run_at is not None


@async_test
async def test_planner_scheduler_daemon_start_stop_and_join_runs_until_stopped() -> None:
    runtime = RecordingPlannerRuntime()
    daemon = PlannerSchedulerDaemon(
        runtime=runtime,
        plan_ids=("plan_1",),
        poll_interval_seconds=0.001,
    )

    await daemon.start()
    try:
        await asyncio.wait_for(runtime.second_call.wait(), timeout=1.0)
        assert daemon.is_running() is True
    finally:
        await daemon.stop()
        joined = await daemon.join(timeout=1.0)

    assert joined is True
    state = daemon.state()
    assert state.status == "stopped"
    assert state.iterations >= 2
    assert [call["plan_id"] for call in runtime.calls[:2]] == [
        "plan_1",
        "plan_1",
    ]
    assert daemon.is_running() is False


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"plan_ids": ()}, "plan_ids"),
        ({"plan_ids": ("",)}, "plan_ids"),
        (
            {"plan_ids": ("plan_1",), "poll_interval_seconds": -1.0},
            "poll_interval_seconds",
        ),
        ({"plan_ids": ("plan_1",), "retry_limit": 0}, "retry_limit"),
        ({"plan_ids": ("plan_1",), "dispatch_limit": 0}, "dispatch_limit"),
    ],
)
def test_planner_scheduler_daemon_rejects_invalid_configuration(
    kwargs: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        PlannerSchedulerDaemon(runtime=RecordingPlannerRuntime(), **kwargs)


def test_runtime_execution_core_does_not_import_planner_scheduler_daemon() -> None:
    project_root = Path(__file__).resolve().parents[2]

    for path in [
        project_root / "src" / "agentos" / "runtime" / "query_loop.py",
        project_root / "src" / "agentos" / "runtime" / "provider_attempt.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "PlannerSchedulerDaemon" not in text
        assert "PlannerSchedulerDaemonState" not in text
        assert "PlannerSchedulerDaemonStatus" not in text
