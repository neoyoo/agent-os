# Planner Worker Dispatch Supervision Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deployment-facing planner worker dispatch supervision profile that summarizes claim-before-tick scheduler reports without owning process supervision.

**Architecture:** Implement a small dataclass in `src/agentos/multi/planner.py` that consumes `PlanClaimedSchedulerTickReport` values and emits JSON-safe health/readiness payloads. Export it from `agentos.multi` and top-level `agentos`, then update readiness/docs/skill guidance.

**Tech Stack:** Python dataclasses, existing planner report types, pytest, AgentOS public API export tests.

---

### Task 1: Red Tests

**Files:**
- Modify: `tests/multi/test_planner_runtime.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing profile tests**

Add tests that instantiate `PlannerWorkerDispatchSupervisionProfile` with
`PlanClaimedSchedulerTickReport` values, then assert missing components,
healthy report summaries, and consecutive `tick-failed` batches.

- [ ] **Step 2: Write failing public API tests**

Add `PlannerWorkerDispatchSupervisionProfile` to the expected `agentos.multi`
and top-level `agentos` public exports.

- [ ] **Step 3: Run red tests**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py -q
```

Expected: FAIL because `PlannerWorkerDispatchSupervisionProfile` does not exist.

### Task 2: Minimal Implementation

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`

- [ ] **Step 1: Add the profile dataclass**

Implement `PLANNER_WORKER_DISPATCH_SUPERVISION_REQUIRED_COMPONENTS` and
`PlannerWorkerDispatchSupervisionProfile` with:

- `missing_components()`
- `health_payload()`
- `readiness_metadata()`
- `readiness_check()`

- [ ] **Step 2: Export the profile**

Add imports and `__all__` entries in `agentos.multi` and top-level `agentos`.

- [ ] **Step 3: Run green tests**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py -q
```

Expected: PASS.

### Task 3: Documentation And Readiness

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Add doc/readiness red assertions**

Require the new profile name, phase entry, and completion estimate in docs and
readiness tests.

- [ ] **Step 2: Update docs and skill guidance**

Document how the profile consumes `PlanClaimedSchedulerTickReport` history and
keeps process supervision deployment-owned.

- [ ] **Step 3: Run focused docs tests**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: PASS.

### Task 4: Verification

**Files:**
- No additional edits expected.

- [ ] **Step 1: Run focused planner verification**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\multi\test_planner_scheduler_daemon.py tests\architecture\test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlannerWorkerDispatchSupervisionProfile|PlanClaimedSchedulerTick|PlanClaim|PlannerSchedulerDaemon" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: tests and compileall pass; boundary scan exits `1` with no output;
diff check exits `0` except pre-existing CRLF warnings.

