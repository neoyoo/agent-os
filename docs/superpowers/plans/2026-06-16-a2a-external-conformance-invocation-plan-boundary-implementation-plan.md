# A2A External Conformance Invocation Plan Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned A2A external conformance invocation plan and preflight gate without owning suite execution or certification.

**Architecture:** Extend `agentos.channels.a2a_conformance` with immutable JSON-safe dataclasses that sit before the existing execution record/gate boundary. Public exports, readiness metadata, docs, and skill guidance make the boundary discoverable while preserving deployment-owned execution responsibilities.

**Tech Stack:** Python dataclasses, pytest, existing `agentos.channels` public API exports, Markdown docs.

---

### Task 1: Invocation Plan Tests

**Files:**
- Modify: `tests/channels/test_a2a_conformance.py`

- [ ] **Step 1: Write failing tests for the desired API**

Add tests for `A2AExternalConformanceInvocationPlan`,
`A2AExternalConformanceInvocationGateReport`, missing policy components,
execution-record projection, and invalid values.

- [ ] **Step 2: Run RED verification**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: FAIL because the new invocation classes do not exist yet.

### Task 2: Invocation Plan Implementation

**Files:**
- Modify: `src/agentos/channels/a2a_conformance.py`

- [ ] **Step 1: Implement the plan dataclass**

Add `A2AExternalConformanceInvocationPlan` with validation,
`configured_component_names()`, `missing_components()`, `as_dict()`,
`readiness_metadata()`, `readiness_check()`, and `to_execution_record(...)`.

- [ ] **Step 2: Implement the gate report dataclass**

Add `A2AExternalConformanceInvocationGateReport.from_plan(...)` with
readiness status, JSON-safe payload, SDK-owned responsibility metadata, and
deployment-owned responsibility metadata.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: PASS for the conformance suite tests.

### Task 3: Public API Exports

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Export the new classes**

Import and list `A2AExternalConformanceInvocationPlan` and
`A2AExternalConformanceInvocationGateReport` in channel and top-level public
APIs.

- [ ] **Step 2: Add public API assertions**

Update the architecture public API test to assert both names exist in
`agentos.channels` and `agentos`.

- [ ] **Step 3: Run public API tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: PASS.

### Task 4: Readiness And Docs

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`

- [ ] **Step 1: Update readiness evidence**

Add invocation plan and gate report evidence to the A2A discovery form, and
move external suite selection/invocation from an unbounded missing primitive to
a deployment-owned execution responsibility around the SDK invocation plan.

- [ ] **Step 2: Update docs and skill guidance**

Document the new preflight boundary and preserve the no-certification-claim
language.

- [ ] **Step 3: Run docs/readiness tests**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: PASS.

### Task 5: Final Verification

**Files:**
- No additional edits.

- [ ] **Step 1: Run focused phase suite**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run runtime boundary scan**

Run:

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlannerClaimedSchedulerDaemon|PlannerSchedulerGovernanceDeploymentProfile|PlanClaim|PostgresPlanClaimStore|PlanClaimedSchedulerTick|PlannerWorkerDispatchSupervisionProfile|PlanClaimSweep|PlannerStaleClaimSweepProfile|PlanDecompositionGate" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no matches.

- [ ] **Step 3: Run full verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q src tests
git diff --check
```

Expected: tests pass, compileall exits 0, and diff check reports no whitespace
errors.
