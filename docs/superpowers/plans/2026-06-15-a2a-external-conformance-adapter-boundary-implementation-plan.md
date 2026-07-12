# A2A External Conformance Adapter Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned adapter for importing external A2A conformance suite results into `A2AConformanceReport`.

**Architecture:** Keep execution of external suites deployment/CI-owned. Add parsing and normalization to `agentos.channels.a2a_conformance`, reuse existing finding/report dataclasses, and retain source metadata on the report.

**Tech Stack:** Python dataclasses, JSON parsing, existing A2A conformance model, pytest.

---

### Task 1: External Report Importer

**Files:**
- Modify: `src/agentos/channels/a2a_conformance.py`
- Test: `tests/channels/test_a2a_conformance.py`

- [ ] **Step 1: Write failing importer tests**

Add tests for canonical JSON, alias fields/status strings, and malformed input.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests\channels\test_a2a_conformance.py -q`

Expected: fails because `A2AExternalConformanceReportImporter` and
`A2AExternalConformanceImportError` do not exist.

- [ ] **Step 3: Implement minimal importer**

Parse JSON or mapping inputs, normalize checks into `A2AConformanceFinding`,
and return `A2AConformanceReport` with metadata fields.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests\channels\test_a2a_conformance.py -q`

Expected: all conformance tests pass.

### Task 2: Public API And Readiness

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Write failing public/readiness assertions**

Assert the importer/error exports exist and readiness evidence mentions external
conformance result import.

- [ ] **Step 2: Verify RED**

Run targeted public/readiness tests.

- [ ] **Step 3: Export and document**

Update exports, readiness evidence, production docs, skill guidance, and
roadmap.

- [ ] **Step 4: Verify GREEN**

Run targeted public/readiness/doc tests.

## Verification Commands

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```
