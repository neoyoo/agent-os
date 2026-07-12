# Planner Schedulable Plan Selection Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow planner runtime/tool boundary that reports which plans have dependency-ready or due-retry work without owning distributed scheduling.

**Architecture:** `PlannerRuntime.schedulable_plans(...)` will compose existing `list_plans`, `ready_steps`, and `retryable_steps` behavior, returning immutable JSON-safe `PlannerSchedulablePlan` summaries. `PlannerTools` will expose this query through an owner-scoped `plan_schedulable_plans` tool; docs and readiness artifacts will state that claim/lock/leader-election mechanics remain deployment-owned.

**Tech Stack:** Python dataclasses, existing `agentos.multi.planner` protocols, pytest.

---

### Task 1: Runtime Schedulable Plan Summary

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests that create plans with ready pending work, due retry work, blocked work, assigned work, terminal status, and another owner. Assert that `runtime.schedulable_plans(...)` returns only summaries for selected owner/status filters, preserves store order, applies limit after filtering, and rejects empty/unknown statuses plus non-positive limits.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: failure because `PlannerRuntime.schedulable_plans` and `PlannerSchedulablePlan` are not implemented.

- [ ] **Step 3: Implement minimal runtime code**

Add `PlannerSchedulablePlanReason`, `PLAN_STATUSES`, `PlannerSchedulablePlan.as_dict()`, status validation, and `PlannerRuntime.schedulable_plans(...)` using existing runtime query methods.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: all planner runtime tests pass.

### Task 2: Owner-Scoped Planner Tool

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_tools.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `plan_schedulable_plans` is registered, returns JSON-safe summaries, accepts `statuses` and `limit`, and cannot expose another owner plan.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\multi\test_planner_tools.py -q
```

Expected: failure because the tool is not registered.

- [ ] **Step 3: Implement minimal tool code**

Register `plan_schedulable_plans`, parse `statuses` and `limit`, call `runtime.schedulable_plans(owner_agent_id=self.owner_agent_id, ...)`, and serialize summary `as_dict()` payloads.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
uv run pytest tests\multi\test_planner_tools.py -q
```

Expected: all planner tool tests pass.

### Task 3: Public API And Guidance

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`

- [ ] **Step 1: Write failing public/guidance tests**

Update public API and docs tests to require the new summary, tool name, readiness evidence, and objective coverage estimate.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: failures for missing exports and docs text.

- [ ] **Step 3: Update exports and guidance**

Export `PlannerSchedulablePlan` and `PlannerSchedulablePlanReason`, add readiness evidence for schedulable-plan selection, document the boundary, update the roadmap with Phase 73, and raise objective coverage from `86%` to `87%`.

- [ ] **Step 4: Verify GREEN and guardrails**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\multi\test_planner_scheduler_daemon.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: tests and compile pass; runtime boundary scan exits with code `1` and no output; `git diff --check` exits `0` even if it prints existing CRLF warnings.
