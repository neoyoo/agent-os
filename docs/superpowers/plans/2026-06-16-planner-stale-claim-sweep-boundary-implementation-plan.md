# Planner Stale Claim Sweep Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a race-safe SDK boundary for reporting and releasing expired planner claim leases without owning deployment scheduling.

**Architecture:** Extend planner claim stores with an optional stale-sweep protocol, add report/profile dataclasses in `src/agentos/multi/planner.py`, implement the operation for in-memory and Postgres stores, and expose the public API. Keep cron, leader election, fairness, and compensation policy deployment-owned.

**Tech Stack:** Python dataclasses, Protocols, existing planner claim stores, parameterized Postgres SQL, pytest.

---

### Task 1: Red Tests

**Files:**
- Modify: `tests/multi/test_planner_runtime.py`
- Modify: `tests/multi/test_postgres_plan_claim_store.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing runtime tests**

Add tests for `PlannerRuntime.sweep_expired_claims(...)` that verify dry-run
does not release claims, release mode deletes only expired claims in scope, and
stale release is generation-safe.

- [ ] **Step 2: Write failing Postgres adapter tests**

Add tests for `PostgresPlanClaimStore.expired_claims(...)` and
`release_expired_claim(...)`, including SQL assertions for parameterized filters
and guarded delete conditions.

- [ ] **Step 3: Write failing public API tests**

Require `PlanClaimSweepReport`, `PlanClaimSweepSkip`, `PlanClaimSweepStore`,
and `PlannerStaleClaimSweepProfile` from `agentos.multi` and top-level
`agentos`.

- [ ] **Step 4: Run red tests**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_postgres_plan_claim_store.py tests\architecture\test_public_api.py -q
```

Expected: FAIL because stale-claim sweep APIs do not exist.

### Task 2: Minimal Implementation

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Modify: `src/agentos/multi/postgres_plan.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`

- [ ] **Step 1: Add report/profile dataclasses and protocol**

Implement `PlanClaimSweepSkip`, `PlanClaimSweepReport`,
`PlanClaimSweepStore`, and `PlannerStaleClaimSweepProfile`.

- [ ] **Step 2: Implement in-memory stale sweep operations**

Add `expired_claims(...)` and `release_expired_claim(...)` to
`InMemoryPlanClaimStore`.

- [ ] **Step 3: Implement runtime sweep composition**

Add `PlannerRuntime.sweep_expired_claims(...)` with validation, dry-run, and
guarded release semantics.

- [ ] **Step 4: Implement Postgres stale sweep operations**

Add parameterized `expired_claims(...)` and guarded
`release_expired_claim(...)` methods to `PostgresPlanClaimStore`.

- [ ] **Step 5: Export public API**

Update `agentos.multi` and top-level `agentos` imports and `__all__`.

- [ ] **Step 6: Run green tests**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_postgres_plan_claim_store.py tests\architecture\test_public_api.py -q
```

Expected: PASS.

### Task 3: Readiness And Documentation

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Add doc/readiness red assertions**

Require stale claim sweep evidence, profile name, and updated completion
estimate.

- [ ] **Step 2: Update docs and skill guidance**

Document the boundary as SDK-owned report/release primitives with
deployment-owned scheduling, locks, leader election, alerting, and
compensation policy.

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
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\multi\test_planner_scheduler_daemon.py tests\multi\test_postgres_plan_claim_store.py tests\architecture\test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanClaimSweep|PlannerStaleClaimSweepProfile|PlanClaimedSchedulerTick|PlanClaim|PlannerSchedulerDaemon" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: tests and compileall pass; boundary scan exits `1` with no output;
diff check exits `0` except pre-existing CRLF warnings.
