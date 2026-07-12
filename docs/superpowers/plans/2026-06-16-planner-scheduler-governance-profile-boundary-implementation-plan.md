# Planner Scheduler Governance Profile Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a planner scheduler governance readiness profile for production multi-node planner deployments.

**Architecture:** Extend `agentos.multi.planner` with a small deployment profile modeled after existing planner readiness profiles. The profile is evidence-only: it reports required/configured governance components and keeps tenant routing, global fairness, distributed locks, leader election, stale lease recovery, process supervision, and live backend verification deployment-owned.

**Tech Stack:** Python dataclasses, pytest, existing public API/readiness/doc tests.

---

### Task 1: Planner Scheduler Governance Profile Tests

**Files:**
- Create: `tests/multi/test_planner_scheduler_governance_profile.py`

- [ ] **Step 1: Write RED tests**

Add tests for:

- default profile reports all required governance components as missing;
- fully configured profile reports `ok` and `ready`;
- validation rejects blank `probe_name`, empty `required_components`, and blank component names;
- runtime query loops do not import planner scheduler governance symbols.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests\multi\test_planner_scheduler_governance_profile.py -q
```

Expected: fail because `PlannerSchedulerGovernanceDeploymentProfile` is not importable.

### Task 2: Runtime Profile API

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_scheduler_governance_profile.py`

- [ ] **Step 1: Add required component constant**

Add `PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS` with:

- `plan_discovery_policy`
- `tenant_routing_policy`
- `global_fairness_policy`
- `scheduler_lock_policy`
- `leader_election_policy`
- `stale_lease_recovery_policy`
- `worker_dispatch_supervision`
- `live_backend_verification`

- [ ] **Step 2: Add profile dataclass**

Add `PlannerSchedulerGovernanceDeploymentProfile` with:

- `missing_components()`
- `readiness_metadata()`
- `readiness_check()`
- component validation

- [ ] **Step 3: Run GREEN**

```powershell
uv run pytest tests\multi\test_planner_scheduler_governance_profile.py -q
```

Expected: pass.

### Task 3: Public API And Readiness

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `tests/test_readiness.py`

- [ ] **Step 1: Add RED assertions**

Require `PlannerSchedulerGovernanceDeploymentProfile` in public exports and
planner readiness evidence.

- [ ] **Step 2: Add exports and readiness evidence**

Export the profile and add it to planner intent-router production evidence,
while preserving the deployment-owned governance caveat.

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
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Add RED doc assertions**

Require `PlannerSchedulerGovernanceDeploymentProfile`,
`planner scheduler governance profile`, `plan_discovery_policy`,
`tenant_routing_policy`, `global_fairness_policy`, `leader_election_policy`,
and `live_backend_verification`.

- [ ] **Step 2: Update docs and skill guidance**

Add Phase 82 to the roadmap and explain that scheduler governance is now a
profile/readiness boundary, not a full distributed scheduler implementation.

- [ ] **Step 3: Run docs tests**

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: pass.

### Task 5: Final Verification

Run:

```powershell
uv run pytest tests\multi\test_planner_scheduler_governance_profile.py tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlannerClaimedSchedulerDaemon|PlannerSchedulerGovernanceDeploymentProfile|PlanClaim|PostgresPlanClaimStore|PlanClaimedSchedulerTick|PlannerWorkerDispatchSupervisionProfile|PlanClaimSweep|PlannerStaleClaimSweepProfile|PlanDecompositionGate" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
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
