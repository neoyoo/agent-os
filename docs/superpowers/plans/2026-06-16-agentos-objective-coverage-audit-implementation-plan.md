# AgentOS Objective Coverage Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested objective coverage audit that maps the original AgentOS SDK review objective to current evidence, coverage level, remaining blockers, and next priorities.

**Architecture:** This phase is documentation and verification only. It creates a single objective audit document, references it from production readiness guidance, and appends Phase 67 to the long-running roadmap. Tests lock coverage so future phases can update the ledger deliberately.

**Tech Stack:** Markdown docs, pytest docs tests, existing roadmap and production readiness files.

---

## File Structure

- Create `docs/agentos-objective-coverage-audit.md`: authoritative objective coverage ledger.
- Create `tests/docs/test_objective_coverage_audit_docs.py`: docs tests for objective coverage.
- Modify `docs/production-readiness.md`: point production readers to the audit.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 67.

## Task 1: RED Objective Coverage Docs Test

- [ ] Add `tests/docs/test_objective_coverage_audit_docs.py`.
- [ ] Require the audit file to mention each original objective area.
- [ ] Require key evidence artifacts and remaining blockers.
- [ ] Require `docs/production-readiness.md` to reference the audit.
- [ ] Require the roadmap to contain `Phase 67`.
- [ ] Run:

```powershell
uv run pytest tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: fails because the audit file and references do not exist yet.

## Task 2: GREEN Objective Audit Document

- [ ] Add `docs/agentos-objective-coverage-audit.md`.
- [ ] Include scoring definitions, current completion estimate, coverage rows, remaining blockers, and next priorities.
- [ ] Keep statuses conservative: direct, primitives-ready, deployment-owned, or future-extension.
- [ ] Run:

```powershell
uv run pytest tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: still fails until production readiness and roadmap references are added.

## Task 3: Link Production Docs And Roadmap

- [ ] Add an audit reference to `docs/production-readiness.md`.
- [ ] Append Phase 67 to `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`.
- [ ] Run:

```powershell
uv run pytest tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: docs test passes.

## Task 4: Verification

- [ ] Run targeted docs/readiness tests:

```powershell
uv run pytest tests\docs\test_objective_coverage_audit_docs.py tests\docs\test_production_readiness_docs.py tests\test_readiness.py -q
```

- [ ] Run full tests:

```powershell
uv run pytest -q
```

- [ ] Compile:

```powershell
uv run python -m compileall -q src tests
```

- [ ] Runtime boundary scan:

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no output.

- [ ] Diff hygiene:

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if the command exits 0.

