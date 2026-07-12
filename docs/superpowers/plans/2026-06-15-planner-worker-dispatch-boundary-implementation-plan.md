# Planner Worker Dispatch Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow planner boundary that dispatches dependency-ready plan steps through the existing coordinator assignment path and returns an auditable report.

**Architecture:** `PlannerRuntime` keeps `PlanStore` as the truth source and reuses `assign_step(...)` for actual coordinator submission. A transient `PlanDispatchReport` reports assigned and skipped steps without adding a scheduler loop or coupling planner behavior to `QueryLoop`.

**Tech Stack:** Python dataclasses, existing `PlanCoordinator` protocol, `ToolRegistry`, pytest.

---

### Task 1: Runtime Dispatch Report

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Add tests covering a bounded dispatch batch, missing-template skips, and coordinator failure skips.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests\multi\test_planner_runtime.py -q`

Expected: fails because `PlannerRuntime.dispatch_ready_steps`, `PlanDispatchReport`, and `PlanDispatchSkip` do not exist.

- [ ] **Step 3: Implement minimal runtime behavior**

Add `PlanDispatchSkip`, `PlanDispatchReport`, and `PlannerRuntime.dispatch_ready_steps(...)`. Reuse `assign_step(...)` for successful dispatch.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests\multi\test_planner_runtime.py -q`

Expected: all planner runtime tests pass.

### Task 2: Planner Tool Boundary

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_tools.py`

- [ ] **Step 1: Write failing tool tests**

Add registration, owner isolation, and JSON report assertions for `plan_dispatch_ready_steps`.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests\multi\test_planner_tools.py -q`

Expected: fails because the tool is not registered.

- [ ] **Step 3: Implement tool handler and schema**

Register `plan_dispatch_ready_steps`, owner-scope it through `_require_owned_plan`, call `dispatch_ready_steps`, and serialize the transient report.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests\multi\test_planner_tools.py -q`

Expected: all planner tool tests pass.

### Task 3: Public API And Docs

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Write failing public API assertions**

Assert `PlanDispatchReport` and `PlanDispatchSkip` are exported from `agentos.multi` and top-level `agentos`.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests\architecture\test_public_api.py -q`

Expected: fails because the new names are not exported.

- [ ] **Step 3: Export and document**

Update package exports, readiness evidence, production docs, skill guidance, and roadmap.

- [ ] **Step 4: Verify GREEN**

Run targeted docs/public tests:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all targeted tests pass.

## Verification Commands

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\examples\test_planner_patterns.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```
