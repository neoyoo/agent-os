# Planner Plan Claim Lease Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow planner plan claim/lease primitive so scheduler workers can avoid concurrently ticking the same schedulable plan.

**Architecture:** Keep claim state separate from `PlanStore`. `PlanStore` remains the truth source for plan state, while `PlanClaimStore` owns transient lease records. `PlannerRuntime` composes Phase 73 schedulable-plan selection with claim acquisition, and `PlannerTools` exposes the same operation as an owner-scoped tool.

**Tech Stack:** Python dataclasses/protocols, existing `agentos.multi.planner` runtime, `ToolRegistry`, pytest.

---

### Task 1: Claim Store Contract

**Files:**
- Modify: `tests/multi/test_planner_runtime.py`
- Modify: `src/agentos/multi/planner.py`

- [ ] **Step 1: Write failing tests**

Add tests for `InMemoryPlanClaimStore` claim, busy, renewal, release, expiry, and invalid input behavior.

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests\multi\test_planner_runtime.py -q`

Expected: import or attribute failures for `InMemoryPlanClaimStore`.

- [ ] **Step 3: Implement minimal claim store**

Add `PlanClaimStatus`, `PlanClaimRecord`, `PlanClaimResult`,
`PlanClaimStore`, and `InMemoryPlanClaimStore` to
`src/agentos/multi/planner.py`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests\multi\test_planner_runtime.py -q`

Expected: runtime tests pass.

### Task 2: Runtime Claim Composition

**Files:**
- Modify: `tests/multi/test_planner_runtime.py`
- Modify: `src/agentos/multi/planner.py`

- [ ] **Step 1: Write failing tests**

Add tests for `PlannerRuntime.claim_schedulable_plans(...)` requiring an
injected claim store, respecting owner/status/limit filters, and returning busy
results for already claimed plans.

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests\multi\test_planner_runtime.py -q`

Expected: `PlannerRuntime` lacks claim-store constructor support or
`claim_schedulable_plans`.

- [ ] **Step 3: Implement runtime helper**

Add optional `claim_store` to `PlannerRuntime.__init__` and implement
`claim_schedulable_plans(...)` by calling `schedulable_plans(...)`, then
`claim_store.claim_plan(...)` with the runtime clock.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests\multi\test_planner_runtime.py -q`

Expected: runtime tests pass.

### Task 3: Planner Tool and Public API

**Files:**
- Modify: `tests/multi/test_planner_tools.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/multi/planner.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`

- [ ] **Step 1: Write failing tests**

Require `plan_claim_schedulable_plans` registration, owner-scoped output, input
validation, and public exports.

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests\multi\test_planner_tools.py tests\architecture\test_public_api.py -q`

Expected: missing tool registration and missing exports.

- [ ] **Step 3: Implement tool and exports**

Register `plan_claim_schedulable_plans`, add parameters for `worker_id`,
`lease_seconds`, `statuses`, and `limit`, and export the claim types from
`agentos.multi` and top-level `agentos`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests\multi\test_planner_tools.py tests\architecture\test_public_api.py -q`

Expected: tests pass.

### Task 4: Readiness and Documentation

**Files:**
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`

- [ ] **Step 1: Write failing docs/readiness tests**

Update tests to require `PlanClaimStore`, `InMemoryPlanClaimStore`,
`PlannerRuntime.claim_schedulable_plans`, `plan_claim_schedulable_plans`, and
an overall completion estimate of `88%`.

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q`

Expected: docs/readiness text has not yet been updated.

- [ ] **Step 3: Update docs and skill guidance**

Document claim/lease as SDK-owned local primitive and keep production
distributed locks, leader election, tenant authorization, fairness, process
supervision, and live backend verification deployment-owned.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q`

Expected: tests pass.

### Task 5: Final Verification

**Files:**
- Inspect: `src/agentos/runtime/query_loop.py`
- Inspect: `src/agentos/runtime/async_query_loop.py`

- [ ] **Step 1: Run focused phase verification**

Run: `uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\multi\test_planner_scheduler_daemon.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q`

Expected: all focused tests pass.

- [ ] **Step 2: Run full suite**

Run: `uv run pytest -q`

Expected: all tests pass or only documented skips remain.

- [ ] **Step 3: Run compile and boundary checks**

Run:

```powershell
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlanClaim" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: compile succeeds, runtime boundary scan has no output and exit code
`1`, and `git diff --check` exits `0` aside from existing CRLF warnings.
