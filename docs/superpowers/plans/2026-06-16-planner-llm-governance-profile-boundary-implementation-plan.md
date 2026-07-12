# Planner LLM Governance Profile Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned, JSON-safe planner LLM governance reference profile for production planner and intent-router deployments.

**Architecture:** Implement a lightweight dataclass in `agentos.multi.planner` beside existing planner deployment profiles. The class reports configured/missing governance references and readiness payloads without executing prompt, model, approval, evaluation, rollout, rollback, or live-backend logic.

**Tech Stack:** Python dataclasses, existing pytest suite, existing readiness/docs/public API checks.

---

### Task 1: Planner Governance Profile API

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests that import `PlannerLlmDecompositionGovernanceProfile`, assert missing components when no refs are configured, assert readiness when all refs are configured, assert evidence refs/metadata are surfaced, and assert empty refs are rejected.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py::test_planner_llm_governance_profile_reports_missing_references tests\multi\test_planner_runtime.py::test_planner_llm_governance_profile_marks_ready_with_policy_refs tests\multi\test_planner_runtime.py::test_planner_llm_governance_profile_rejects_invalid_refs -q
```

Expected: fail because `PlannerLlmDecompositionGovernanceProfile` is not defined.

- [ ] **Step 3: Implement minimal class**

Add `PLANNER_LLM_DECOMPOSITION_GOVERNANCE_REQUIRED_COMPONENTS` and `PlannerLlmDecompositionGovernanceProfile` with `configured_component_names`, `missing_components`, `readiness_metadata`, and `readiness_check`.

- [ ] **Step 4: Verify GREEN**

Run the same targeted tests. Expected: pass.

### Task 2: Public API And Readiness

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `tests/test_readiness.py`

- [ ] **Step 1: Write failing public API/readiness assertions**

Assert the new profile is exported from `agentos.multi` and `agentos`, and appears in planner/intent-router readiness evidence.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py -q
```

Expected: fail until exports and readiness evidence are updated.

- [ ] **Step 3: Export and wire readiness**

Add the class to imports and `__all__`, and update planner readiness evidence/gaps so the profile is SDK-owned while actual policy execution remains deployment-owned.

- [ ] **Step 4: Verify GREEN**

Run the same tests. Expected: pass.

### Task 3: Docs And Skill Guidance

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Write failing docs assertions**

Assert production docs, objective audit, roadmap, and SDK skill guidance mention `PlannerLlmDecompositionGovernanceProfile`, governance references, and deployment-owned execution boundaries.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: fail until docs are updated.

- [ ] **Step 3: Update docs and skill guidance**

Document how to use the profile before deployment-owned LLM planner execution and clarify that prompt/model/approval/evaluation execution remains deployment-owned.

- [ ] **Step 4: Verify GREEN**

Run the same docs tests. Expected: pass.

### Task 4: Final Verification

- [ ] **Step 1: Run focused suite**

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

- [ ] **Step 2: Run runtime boundary scan**

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlannerClaimedSchedulerDaemon|PlannerSchedulerGovernanceDeploymentProfile|PlanClaim|PostgresPlanClaimStore|PlanClaimedSchedulerTick|PlannerWorkerDispatchSupervisionProfile|PlanClaimSweep|PlannerStaleClaimSweepProfile|PlanDecompositionGate|PlannerLlmDecompositionGovernanceProfile" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1, no output.

- [ ] **Step 3: Run full suite**

```powershell
uv run pytest -q
uv run python -m compileall -q src tests
git diff --check
```

Expected: pytest and compileall exit 0; `git diff --check` has no whitespace errors apart from existing CRLF warnings if present.
