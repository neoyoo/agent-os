# Planner LLM Governance Execution Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-proposal planner LLM governance evidence gate so production plan creation can be blocked when prompt/model/approval/evaluation/validation evidence is missing or failed.

**Architecture:** Extend `agentos.multi.planner` with immutable JSON-safe evidence record and gate report dataclasses plus a `PlannerRuntime.gate_llm_governance_evidence(...)` helper. The SDK consumes external evidence references and statuses; deployment-owned systems still execute prompts, route models, approve, evaluate, validate, store artifacts, and certify releases.

**Tech Stack:** Python dataclasses, existing planner runtime patterns, pytest, public API tests, docs readiness checks.

---

### Task 1: Planner LLM Governance Evidence Record And Gate

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Add tests that import `PlannerLlmGovernanceEvidenceRecord` and
`PlannerLlmGovernanceEvidenceGateReport`, then assert:

- a record with prompt/model/approval/evaluation/validation refs and passing
  statuses produces an accepted gate report;
- missing approval evidence blocks plan creation;
- failed evaluation evidence blocks plan creation;
- invalid empty refs and non-JSON metadata are rejected;
- `as_dict()` payloads are JSON-safe and do not include raw prompt text or
  secret values.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py::test_planner_llm_governance_evidence_gate_accepts_complete_external_evidence tests\multi\test_planner_runtime.py::test_planner_llm_governance_evidence_gate_blocks_missing_approval_evidence tests\multi\test_planner_runtime.py::test_planner_llm_governance_evidence_gate_blocks_failed_evaluation tests\multi\test_planner_runtime.py::test_planner_llm_governance_evidence_record_rejects_invalid_refs_and_metadata tests\multi\test_planner_runtime.py::test_planner_llm_governance_evidence_payload_omits_raw_prompt_and_secrets -q
```

Expected: fail because the new record/report/runtime method do not exist.

- [ ] **Step 3: Implement minimal planner API**

Add:

- `PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE`
- `PlannerLlmGovernanceEvidenceRecord`
- `PlannerLlmGovernanceEvidenceGateReport`
- `PlannerRuntime.gate_llm_governance_evidence(...)`

Validate required strings, JSON metadata, and status fields. The method must not
call `create_plan_from_decomposition(...)` or mutate `PlanStore`.

- [ ] **Step 4: Verify GREEN**

Run the same targeted tests. Expected: pass.

### Task 2: Public API Exports

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Test: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing export assertions**

Assert `PlannerLlmGovernanceEvidenceRecord` and
`PlannerLlmGovernanceEvidenceGateReport` exist on both `agentos.multi` and
top-level `agentos`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: fail until exports are added.

- [ ] **Step 3: Export the new public names**

Import and add the new names to `__all__` in `agentos.multi` and top-level
`agentos`.

- [ ] **Step 4: Verify GREEN**

Run the same public API test. Expected: pass.

### Task 3: Docs, Roadmap, Audit, And Skill Guidance

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Test: `tests/docs/test_production_readiness_docs.py`
- Test: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Write failing docs assertions**

Assert production docs and objective audit mention
`PlannerLlmGovernanceEvidenceRecord`,
`PlannerLlmGovernanceEvidenceGateReport`, and
`PlannerRuntime.gate_llm_governance_evidence`, while also naming
deployment-owned prompt/model/approval/evaluation/validation execution.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: fail until docs are updated.

- [ ] **Step 3: Update docs and skill guidance**

Document Phase 92 in the roadmap with target conclusion, artifacts, and
conclusion. Update readiness/audit/skill guidance so planner and intent-router
specs require per-proposal governance evidence before production plan creation.

- [ ] **Step 4: Verify GREEN**

Run the same docs tests. Expected: pass.

### Task 4: Final Verification

- [ ] **Step 1: Run focused verification**

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

- [ ] **Step 2: Run runtime boundary scan**

```powershell
rg -n "PlannerLlmGovernanceEvidence|planner LLM governance execution" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1, no output.

- [ ] **Step 3: Run full repository verification**

```powershell
uv run python -m compileall -q src tests
git diff --check
uv run pytest -q
```

Expected: compileall and pytest exit 0; `git diff --check` has no new
whitespace errors.
