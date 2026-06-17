# A2A External Conformance Execution Record Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a JSON-safe SDK boundary for recording externally executed A2A conformance suite attempts and projecting them into release/readiness gate reports.

**Architecture:** Extend `src/agentos/channels/a2a_conformance.py` with immutable execution-record and gate-report dataclasses that consume the existing imported `A2AConformanceReport`. Export the new API from `agentos.channels` and top-level `agentos`, update readiness/docs/skill guidance, and keep actual suite execution and certification governance deployment-owned.

**Tech Stack:** Python dataclasses, existing A2A conformance report model, pytest, markdown docs.

---

### Task 1: Red Tests

**Files:**
- Modify: `tests/channels/test_a2a_conformance.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Add execution record/gate tests**

Add tests that construct an imported `A2AConformanceReport`, wrap it in
`A2AExternalConformanceExecutionRecord`, and assert that
`A2AExternalConformanceGateReport.from_record(...)` returns a ready JSON-safe
gate when exit code is zero, required checks are present, and required
components are configured.

- [ ] **Step 2: Add negative gate tests**

Add tests for non-zero exit codes, missing reports, failed imported checks,
missing required checks, missing components, empty command values, and invalid
timestamp ordering.

- [ ] **Step 3: Add public API red assertions**

Require `A2AExternalConformanceExecutionRecord` and
`A2AExternalConformanceGateReport` from both `agentos.channels` and top-level
`agentos`.

- [ ] **Step 4: Run red tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\architecture\test_public_api.py -q
```

Expected: FAIL because the new execution record and gate report APIs do not
exist.

### Task 2: Minimal Implementation

**Files:**
- Modify: `src/agentos/channels/a2a_conformance.py`
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`

- [ ] **Step 1: Add `A2AExternalConformanceExecutionRecord`**

Add an immutable dataclass with fields for suite, target, command, exit code,
started/ended timestamps, environment, artifact URI, stdout/stderr summaries,
report, and metadata. Add validation for non-empty suite/target/command,
non-negative exit code, and `ended_at >= started_at` when both timestamps are
present.

- [ ] **Step 2: Add `A2AExternalConformanceGateReport`**

Add an immutable dataclass with `from_record(...)`, `ready`, `as_dict()`,
missing-required-check, failed-required-check, and missing-component metadata.
Keep the payload JSON-safe and include `no_certification_claim`.

- [ ] **Step 3: Export public API**

Update channel and top-level imports and `__all__` entries.

- [ ] **Step 4: Run green tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\architecture\test_public_api.py -q
```

Expected: PASS.

### Task 3: Readiness And Documentation

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Add doc/readiness red assertions**

Require `A2AExternalConformanceExecutionRecord`,
`A2AExternalConformanceGateReport`, "external conformance execution record",
"external conformance gate report", and "no certification claim" in readiness
evidence, production docs, objective audit, and skill guidance.

- [ ] **Step 2: Update docs and skill guidance**

Document that deployments run the suite and provide the execution record; the
SDK only normalizes execution evidence and evaluates local gate readiness.

- [ ] **Step 3: Run focused docs tests**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: PASS.

### Task 4: Verification

**Files:**
- No additional edits expected.

- [ ] **Step 1: Run focused A2A verification**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlanClaim|PostgresPlanClaimStore|PlanClaimedSchedulerTick|PlannerWorkerDispatchSupervisionProfile|PlanClaimSweep|PlannerStaleClaimSweepProfile" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: tests and compileall pass; boundary scan exits `1` with no output;
diff check exits `0` except pre-existing CRLF warnings.

