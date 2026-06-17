# Planner LLM Decomposition Gate Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a JSON-safe SDK gate for raw LLM/main-agent planner decomposition proposals before plan persistence.

**Architecture:** Extend `agentos.multi.planner` with a small policy/report pair and a `PlannerRuntime.gate_decomposition_proposal(...)` method that parses raw proposal mappings into `PlanDecomposition`, applies gate policy, reuses `validate_decomposition(...)`, and returns an auditable report. Expose the same read-only behavior through `PlannerTools` while keeping LLM prompting, approval workflow, and automatic plan creation deployment-owned.

**Tech Stack:** Python dataclasses, pytest, existing AgentOS planner runtime/tools/readiness docs.

---

### Task 1: Runtime Gate Report And Policy

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests that import `PlanDecompositionGatePolicy`, call
`PlannerRuntime.gate_decomposition_proposal(...)`, and assert:

- accepted report for a valid raw mapping includes `normalized_decomposition`;
- invalid raw shape returns `accepted=False`, errors, and no stored plans;
- `require_template`, `allowed_template_ids`, `max_steps`, and approval policy
  produce deterministic report fields.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: fail because the policy/report/runtime method do not exist.

- [ ] **Step 3: Implement minimal runtime API**

In `src/agentos/multi/planner.py` add:

- `PlanDecompositionGatePolicy`
- `PlanDecompositionGateReport`
- `PlanDecompositionGateReport.as_dict()`
- `PlannerRuntime.gate_decomposition_proposal(...)`
- private parsing helpers as needed

The gate must not call `create_plan_from_decomposition(...)` or mutate
`PlanStore`.

- [ ] **Step 4: Run focused tests and verify pass**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: pass.

### Task 2: Planner Tool Boundary

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_tools.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert `PlannerTools.register(...)` includes
`plan_gate_decomposition_proposal`, the handler returns the same gate report
shape for a valid raw proposal, and the tool schema exposes policy fields
without mutating plan state.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests\multi\test_planner_tools.py -q
```

Expected: fail because the tool is not registered.

- [ ] **Step 3: Implement tool registration and handler**

Add `plan_gate_decomposition_proposal` before
`plan_create_from_decomposition`. The handler should call
`PlannerRuntime.gate_decomposition_proposal(...)` and return
`report.as_dict()` as sorted JSON.

- [ ] **Step 4: Run focused tests and verify pass**

Run:

```powershell
uv run pytest tests\multi\test_planner_tools.py -q
```

Expected: pass.

### Task 3: Public API And Readiness Evidence

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `src/agentos/readiness.py`
- Test: `tests/architecture/test_public_api.py`
- Test: `tests/test_readiness.py`

- [ ] **Step 1: Write failing export/readiness assertions**

Assert `PlanDecompositionGatePolicy` and `PlanDecompositionGateReport` exist
on `agentos.multi` and `agentos`. Add readiness evidence for
`PlannerRuntime.gate_decomposition_proposal` and
`plan_gate_decomposition_proposal`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py -q
```

Expected: fail until exports and readiness evidence are updated.

- [ ] **Step 3: Update exports and readiness**

Export the new types and add the gate evidence to the planner-intent-router
form without removing deployment-owned prompt/model/approval/evaluation gaps.

- [ ] **Step 4: Run focused tests and verify pass**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py -q
```

Expected: pass.

### Task 4: Docs, Skill, Roadmap, And Audit

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Test: `tests/docs/test_production_readiness_docs.py`
- Test: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Write failing doc assertions**

Assert docs and skill guidance mention
`PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`,
`PlannerRuntime.gate_decomposition_proposal`, and
`plan_gate_decomposition_proposal`, while still naming deployment-owned
LLM prompt/model/approval/evaluation policy.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: fail until docs are updated.

- [ ] **Step 3: Update docs and skill guidance**

Add Phase 80 to the roadmap with target conclusion, artifacts, and conclusion.
Update readiness/audit/skill wording to classify automatic LLM planning as
deployment-owned and the proposal gate as SDK-owned.

- [ ] **Step 4: Run doc tests and verify pass**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: pass.

### Task 5: Final Verification

**Files:**
- No code edits unless verification exposes issues.

- [ ] **Step 1: Run focused planner suite**

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\architecture\test_public_api.py tests\test_readiness.py -q
```

- [ ] **Step 2: Run boundary scan**

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlanClaim|PostgresPlanClaimStore|PlanClaimedSchedulerTick|PlannerWorkerDispatchSupervisionProfile|PlanClaimSweep|PlannerStaleClaimSweepProfile|PlanDecompositionGate" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit `1`, no output.

- [ ] **Step 3: Run full verification**

```powershell
uv run pytest -q
uv run python -m compileall -q src tests
git diff --check
```

Expected: all tests pass, compileall succeeds, diff check has no errors.
