# SDK Skill Spec Generator Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 99 so the agent-os skill becomes a production agent design constraint generator with a required `production_design_constraints` spec block.

**Architecture:** Keep the gate in documentation and skill guidance. Do not add runtime coupling and do not move state-plane, worker, planner, A2A, readiness, or sandbox concepts into `QueryLoop` or `AsyncQueryLoop`.

**Tech Stack:** Markdown skill/docs, pytest docs tests, existing AgentOS release/readiness documentation.

---

### Task 1: Write RED Docs Tests

**Files:**
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Add Phase 99 assertions**

Add assertions requiring these phrases in production readiness, objective audit,
roadmap, skill, requirements flow, spec generation flow, and implementation
flow:

```text
Phase 99: SDK Skill / Spec Generator Finalization
spec generator finalization
production agent design constraint generator
production_design_constraints
must explicitly choose
agent form
runtime profile
state plane components
persistence backend
registry backend
queue backend
worker supervisor
A2A exposure
planner/team mode
production readiness checklist
sandbox posture: trusted tools only | deployment-owned isolation | future adapter
does not create deployment-owned infrastructure
SDK-owned constraint template
```

- [ ] **Step 2: Run RED tests**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py::test_production_readiness_doc_describes_sdk_spec_generator_finalization tests\docs\test_objective_coverage_audit_docs.py::test_objective_coverage_audit_names_evidence_and_remaining_blockers tests\docs\test_objective_coverage_audit_docs.py::test_production_readiness_and_roadmap_link_objective_audit -q
```

Expected: FAIL because Phase 99 guidance is not present yet.

### Task 2: Update Skill And Docs

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/flow/03-implementation.md`

- [ ] **Step 1: Document Phase 99**

Add Phase 99 text that defines the spec generator finalization gate and
production agent design constraint generator.

- [ ] **Step 2: Add the schema block**

Add `deployment.production_design_constraints` to the spec schema with explicit
choices for agent form, runtime profile, state plane components, persistence
backend, registry backend, queue backend, worker supervisor, A2A exposure,
planner/team mode, production readiness checklist, and sandbox posture: trusted
tools only | deployment-owned isolation | future adapter.

- [ ] **Step 3: Add implementation preflight**

Require implementation guidance to stop before skeleton generation when a
production-bound spec lacks `production_design_constraints`.

### Task 3: Verify

**Files:**
- Test: `tests/docs/test_production_readiness_docs.py`
- Test: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Run targeted docs tests**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py::test_production_readiness_doc_describes_sdk_spec_generator_finalization tests\docs\test_objective_coverage_audit_docs.py::test_objective_coverage_audit_names_evidence_and_remaining_blockers tests\docs\test_objective_coverage_audit_docs.py::test_production_readiness_and_roadmap_link_objective_audit -q
```

Expected: PASS.

- [ ] **Step 2: Run docs test suites**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: PASS.

- [ ] **Step 3: Run boundary scan**

Run:

```powershell
rg -n "ReferenceLiveBackendProbe|REFERENCE_LIVE_BACKEND|ReferenceStatePlane|state plane|readiness|planner|team|A2A|sandbox|worker supervisor|production_design_constraints" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no output, exit code 1.
