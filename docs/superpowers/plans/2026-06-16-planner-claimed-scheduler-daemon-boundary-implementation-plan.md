# Planner Claimed Scheduler Daemon Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned planner daemon that repeatedly invokes `PlannerRuntime.claimed_scheduler_tick(...)` for claim-before-tick scheduler workers.

**Architecture:** Extend `agentos.multi.planner` with claimed-daemon lifecycle dataclasses and a small threaded daemon modeled after `PlannerSchedulerDaemon`. The daemon records immutable local state and delegates plan selection, claims, ticks, and optional release to `PlannerRuntime.claimed_scheduler_tick(...)`, keeping distributed locks, leader election, process supervision, and compensation deployment-owned.

**Tech Stack:** Python dataclasses, `threading.Event`, `threading.Thread`, pytest, existing `PlannerRuntime` and `PlanClaimedSchedulerTickReport`.

---

### Task 1: Claimed Scheduler Daemon Tests

**Files:**
- Create: `tests/multi/test_planner_claimed_scheduler_daemon.py`

- [ ] **Step 1: Write RED tests**

Add tests for:

- `run_once()` passing worker, lease, owner/status filters, limits, default template, and release flag to `PlannerRuntime.claimed_scheduler_tick(...)`;
- lifecycle polling through `start`, `stop`, `join`, and `is_running`;
- runtime error recording;
- invalid configuration;
- query-loop boundary scan.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests\multi\test_planner_claimed_scheduler_daemon.py -q
```

Expected: fail because `PlannerClaimedSchedulerDaemon` is not importable.

### Task 2: Runtime Daemon API

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_claimed_scheduler_daemon.py`

- [ ] **Step 1: Implement dataclasses**

Add:

- `PlannerClaimedSchedulerDaemonStatus`
- `PlannerClaimedSchedulerDaemonError`
- `PlannerClaimedSchedulerDaemonState`

- [ ] **Step 2: Implement daemon**

Add `PlannerClaimedSchedulerDaemon` with:

- `run_once()`
- `start()`
- `stop()`
- `join(timeout)`
- `is_running()`
- `state()`
- private `_run_loop`, `_record_success`, `_record_error`, and validation helpers

- [ ] **Step 3: Run GREEN**

```powershell
uv run pytest tests\multi\test_planner_claimed_scheduler_daemon.py -q
```

Expected: pass.

### Task 3: Public API, Readiness, And Tools Boundary

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `src/agentos/readiness.py`
- Test: `tests/architecture/test_public_api.py`
- Test: `tests/test_readiness.py`

- [ ] **Step 1: Add RED assertions**

Require the claimed daemon names in `agentos.multi`, top-level `agentos`, and
planner readiness evidence.

- [ ] **Step 2: Add exports and readiness evidence**

Export all four new names and update planner readiness so the SDK owns
claim-before-tick daemon polling while distributed locks, tenant policy, process
supervision, and compensation remain deployment-owned.

- [ ] **Step 3: Run focused tests**

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py -q
```

Expected: pass.

### Task 4: Docs, Skill, Audit, And Roadmap

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Test: `tests/docs/test_production_readiness_docs.py`
- Test: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Add RED doc assertions**

Assert docs and skill guidance mention `PlannerClaimedSchedulerDaemon`,
`PlannerClaimedSchedulerDaemonState`, claim-before-tick daemon polling, and the
deployment-owned boundaries.

- [ ] **Step 2: Update docs and skill guidance**

Add Phase 81 to the roadmap and move scheduler-worker polling from a pure gap
to an SDK-owned claimed daemon boundary. Keep process supervision, distributed
locks, tenant policy, and compensation deployment-owned.

- [ ] **Step 3: Run docs tests**

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: pass.

### Task 5: Final Verification

Run:

```powershell
uv run pytest tests\multi\test_planner_claimed_scheduler_daemon.py tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlannerClaimedSchedulerDaemon|PlanClaim|PostgresPlanClaimStore|PlanClaimedSchedulerTick|PlannerWorkerDispatchSupervisionProfile|PlanClaimSweep|PlannerStaleClaimSweepProfile|PlanDecompositionGate" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
uv run pytest -q
uv run python -m compileall -q src tests
git diff --check
```

Expected:

- Focused tests pass.
- Runtime boundary scan exits `1` with no output.
- Full suite passes.
- Compileall exits `0`.
- `git diff --check` exits `0`; CRLF warnings are acceptable in this repository.
