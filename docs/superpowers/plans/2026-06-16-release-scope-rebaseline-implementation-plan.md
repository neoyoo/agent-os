# Release Scope Re-baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define Phase 96: Release Scope Re-baseline as a test-backed release boundary for the first production SDK release.

**Architecture:** This is a documentation and skill-governance phase. It adds a canonical `docs/release-scope.md` file and updates production readiness, objective audit, roadmap, and agent-os skill guidance so all release surfaces state the same boundary. Runtime code remains unchanged.

**Tech Stack:** Markdown docs, pytest documentation tests, PowerShell, `rg`, `uv`.

---

### Task 1: Add Red Documentation Tests

**Files:**
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Add tests that require Phase 96 wording**

Add assertions for:

```python
"Phase 96: Release Scope Re-baseline"
"release scope re-baseline"
"first production SDK release"
"trusted tools"
"internal service orchestration"
"terminal agent"
"single-node web agent"
"distributed web agent"
"team/planner/A2A primitive"
"production state plane"
"readiness evidence"
"Sandbox / Docker / E2B / microVM / enterprise runner adapter"
"non-blocking future adapter"
"not a release blocker"
"does not promise physical isolation for untrusted code execution"
"WorkspaceExecutionBackend"
"SandboxBackend"
"LocalWorkspaceExecutionBackend"
"policy/capability/path pre-check"
"audit evidence"
"sandbox posture"
"trusted tools only"
"deployment-owned isolation"
"future adapter"
"docs/release-scope.md"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py::test_production_readiness_doc_describes_release_scope_rebaseline_boundary tests\docs\test_objective_coverage_audit_docs.py::test_objective_coverage_audit_names_evidence_and_remaining_blockers tests\docs\test_objective_coverage_audit_docs.py::test_production_readiness_and_roadmap_link_objective_audit -q
```

Expected: fail because `docs/release-scope.md`, Phase 96 roadmap text, and
Phase 96 audit text are missing.

### Task 2: Add Release Scope Docs

**Files:**
- Create: `docs/release-scope.md`
- Create: `docs/superpowers/specs/2026-06-16-release-scope-rebaseline-design.md`
- Create: `docs/superpowers/plans/2026-06-16-release-scope-rebaseline-implementation-plan.md`

- [ ] **Step 1: Add canonical release scope**

Write `docs/release-scope.md` with the target conclusion, release-in scope,
release-out scope, sandbox posture, and release gate sections.

- [ ] **Step 2: Add design and implementation docs**

Write the Phase 96 design and this implementation plan so the roadmap can link
both artifacts.

### Task 3: Update Existing Docs And Skill Guidance

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/architecture.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`

- [ ] **Step 1: Add the Phase 96 boundary block**

Add a short block naming the first production SDK release, supported forms,
release-owned evidence, and Sandbox as a non-blocking future adapter.

- [ ] **Step 2: Add sandbox posture to requirements and spec generation**

Require every production-bound spec to choose one of:

```yaml
sandbox_posture: trusted tools only | deployment-owned isolation | future adapter
```

### Task 4: Verify

**Files:**
- Test: `tests/docs/test_production_readiness_docs.py`
- Test: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Run targeted tests**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py::test_production_readiness_doc_describes_release_scope_rebaseline_boundary tests\docs\test_objective_coverage_audit_docs.py::test_objective_coverage_audit_names_evidence_and_remaining_blockers tests\docs\test_objective_coverage_audit_docs.py::test_production_readiness_and_roadmap_link_objective_audit -q
```

Expected: pass.

- [ ] **Step 2: Run compile and diff checks**

Run:

```powershell
uv run python -m compileall -q src tests
git diff --check
```

Expected: compile succeeds; diff check reports no whitespace errors.

- [ ] **Step 3: Run runtime boundary scan**

Run:

```powershell
rg -n "ReleaseScope|release scope|Sandbox|WorkspaceExecutionBackend|SandboxBackend" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches. An `rg` exit code of 1 is acceptable when there are no
matches.

- [ ] **Step 4: Run full tests when time permits**

Run:

```powershell
uv run pytest -q
```

Expected: all tests pass or only pre-existing environmental skips remain.
