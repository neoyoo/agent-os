# Planner Scheduler Daemon Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow SDK-owned planner scheduler daemon that repeatedly invokes `PlannerRuntime.scheduler_tick(...)` for explicitly supplied plan ids.

**Architecture:** Extend `agentos.multi.planner` with daemon lifecycle dataclasses and a small threaded daemon modeled after `TeamWorkerDaemon`. The daemon records immutable state snapshots and keeps plan discovery, distributed locking, process supervision, compensation, credentials, migrations, and live backend verification deployment-owned.

**Tech Stack:** Python dataclasses, `threading.Event`, `threading.Thread`, pytest, existing `PlannerRuntime` and `PlanSchedulerTickReport`.

---

### Task 1: Add Planner Scheduler Daemon Behavior Tests

**Files:**
- Create: `tests/multi/test_planner_scheduler_daemon.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agentos.multi.planner import (
    InMemoryPlanStore,
    PlanSchedulerTickReport,
    PlannerRuntime,
    PlannerSchedulerDaemon,
    PlannerSchedulerDaemonError,
)


class RecordingPlannerRuntime(PlannerRuntime):
    def __init__(self) -> None:
        super().__init__(store=InMemoryPlanStore())
        self.calls: list[dict[str, object]] = []
        self.second_call = threading.Event()
        self.fail_plan_ids: set[str] = set()

    def scheduler_tick(
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
```

- [ ] **Step 2: Add tests for run_once, lifecycle, invalid config, and runtime loop boundary**

```python
def test_planner_scheduler_daemon_run_once_records_reports_and_errors() -> None:
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

    reports = daemon.run_once()
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


def test_planner_scheduler_daemon_start_stop_and_join_runs_until_stopped() -> None:
    runtime = RecordingPlannerRuntime()
    daemon = PlannerSchedulerDaemon(
        runtime=runtime,
        plan_ids=("plan_1",),
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
    assert [call["plan_id"] for call in runtime.calls[:2]] == ["plan_1", "plan_1"]
    assert daemon.is_running() is False


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"plan_ids": ()}, "plan_ids"),
        ({"plan_ids": ("",)}, "plan_ids"),
        ({"plan_ids": ("plan_1",), "poll_interval_seconds": -1.0}, "poll_interval_seconds"),
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


def test_runtime_loops_do_not_import_planner_scheduler_daemon() -> None:
    project_root = Path(__file__).resolve().parents[2]

    for path in [
        project_root / "src" / "agentos" / "runtime" / "query_loop.py",
        project_root / "src" / "agentos" / "runtime" / "async_query_loop.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "PlannerSchedulerDaemon" not in text
        assert "PlannerSchedulerDaemonState" not in text
        assert "PlannerSchedulerDaemonStatus" not in text
```

- [ ] **Step 3: Run RED**

Run: `uv run pytest tests\multi\test_planner_scheduler_daemon.py -q`

Expected: fails because `PlannerSchedulerDaemon` is not importable.

### Task 2: Implement Planner Scheduler Daemon

**Files:**
- Modify: `src/agentos/multi/planner.py`

- [ ] **Step 1: Add imports and types**

Add `Event` and `Thread` to the `threading` import and define:

```python
PlannerSchedulerDaemonStatus = Literal["idle", "running", "stopping", "stopped"]


@dataclass(frozen=True, slots=True)
class PlannerSchedulerDaemonError:
    plan_id: str
    error: str


@dataclass(frozen=True, slots=True)
class PlannerSchedulerDaemonState:
    status: PlannerSchedulerDaemonStatus
    plan_ids: tuple[str, ...]
    poll_interval_seconds: float
    default_template_id: str | None = None
    retry_limit: int | None = None
    dispatch_limit: int | None = None
    iterations: int = 0
    started_at: float | None = None
    stopped_at: float | None = None
    last_run_at: float | None = None
    last_reports: tuple[PlanSchedulerTickReport, ...] = ()
    errors: tuple[PlannerSchedulerDaemonError, ...] = ()
```

- [ ] **Step 2: Add `PlannerSchedulerDaemon`**

Implement `run_once`, `start`, `stop`, `join`, `is_running`, `state`, `_run_loop`, `_record_run`, and validation methods. `run_once` must continue after per-plan exceptions.

- [ ] **Step 3: Run GREEN**

Run: `uv run pytest tests\multi\test_planner_scheduler_daemon.py -q`

Expected: passes.

### Task 3: Export Public API

**Files:**
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`

- [ ] **Step 1: Add public API tests**

Require these names in `agentos.multi` and top-level `agentos` exports:

```python
"PlannerSchedulerDaemon",
"PlannerSchedulerDaemonError",
"PlannerSchedulerDaemonState",
"PlannerSchedulerDaemonStatus",
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests\architecture\test_public_api.py -q`

Expected: fails until exports are added.

- [ ] **Step 3: Add exports**

Import and include all four new names in both public API surfaces.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests\architecture\test_public_api.py -q`

Expected: passes.

### Task 4: Synchronize Readiness, Docs, Skill, Audit, and Roadmap

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Add RED docs/readiness assertions**

Assert the docs and readiness entries mention `PlannerSchedulerDaemon`, `PlannerSchedulerDaemonState`, explicitly supplied plan ids, and deployment-owned distributed locks/plan discovery/process supervision.

- [ ] **Step 2: Update readiness and docs**

Move planner scheduler daemon/loop from a pure gap to an SDK primitive with deployment-owned production responsibilities.

- [ ] **Step 3: Update objective audit**

Raise the completion estimate from `84%` to `86%` only after docs and tests prove the daemon boundary is wired.

- [ ] **Step 4: Append roadmap Phase 72**

Add a phase entry with the target conclusion, artifacts, validation, and next blockers.

### Task 5: Verification

Run:

```powershell
uv run pytest tests\multi\test_planner_scheduler_daemon.py tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected:

- Focused tests pass.
- Full suite passes.
- Compileall exits 0.
- Runtime boundary scan exits 1 with no output.
- `git diff --check` exits 0; CRLF warnings are acceptable in this repository.

