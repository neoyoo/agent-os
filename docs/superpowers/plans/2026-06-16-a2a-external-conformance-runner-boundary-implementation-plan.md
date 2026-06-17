# A2A External Conformance Runner Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned reference CLI runner that executes an external A2A conformance invocation plan and records JSON-safe evidence without owning the suite, CI, credentials, artifacts, or certification.

**Architecture:** Extend `src/agentos/channels/a2a_conformance.py` with a narrow runner protocol and a subprocess-backed CLI adapter that returns the existing `A2AExternalConformanceExecutionRecord`. The adapter imports report JSON from a configured path or stdout, bounds captured output, records timeout/import errors as evidence, exports the public API, and updates production docs and skill guidance.

**Tech Stack:** Python dataclasses, `subprocess.run(shell=False)`, `pathlib.Path`, existing A2A conformance report importer, pytest, markdown docs.

---

### Task 1: Runner Red Tests

**Files:**
- Modify: `tests/channels/test_a2a_conformance.py`

- [ ] **Step 1: Add success tests for report path and stdout import**

Add tests that create an `A2AExternalConformanceInvocationPlan`, run
`A2AExternalConformanceCliRunner` with `sys.executable -c ...`, and assert the
returned `A2AExternalConformanceExecutionRecord` captures exit code, timestamps,
stdout/stderr summaries, imported report, environment label, artifact URI, and
runner metadata.

- [ ] **Step 2: Add failure evidence tests**

Add tests for nonzero exit, malformed report JSON, missing report path, timeout,
bounded output, and invalid runner settings. These tests should assert an
execution record is still returned when the external process itself ran and
reported failure, and that timeout is captured as evidence rather than as a
certification claim.

- [ ] **Step 3: Run RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py::<new_test_name> -q
```

Expected: FAIL because `A2AExternalConformanceCliRunner` and
`A2AExternalConformanceRunner` are not implemented yet.

### Task 2: Runner Implementation

**Files:**
- Modify: `src/agentos/channels/a2a_conformance.py`

- [ ] **Step 1: Add imports and protocol**

Add `subprocess`, `time`, `Path`, `Protocol`, and a narrow
`A2AExternalConformanceRunner` protocol with `run(plan) ->
A2AExternalConformanceExecutionRecord`.

- [ ] **Step 2: Add CLI runner dataclass**

Implement `A2AExternalConformanceCliRunner` with fields:
`timeout_seconds`, `report_path`, `environment`, `artifact_uri`,
`stdout_limit`, `stderr_limit`, `env`, and `report_importer`.

- [ ] **Step 3: Implement `run(plan)`**

Call `subprocess.run(plan.command, shell=False, capture_output=True, text=True,
timeout=timeout_seconds, env=...)`. Capture timestamps, exit code, bounded
output summaries, optional report import, and metadata keys:
`runner`, `timeout_seconds`, `report_source`, `report_import_error`,
`timed_out`, `env_keys`, and `no_certification_claim`.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: PASS.

### Task 3: Public API And Docs

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`

- [ ] **Step 1: Add public API assertions**

Require `A2AExternalConformanceRunner` and
`A2AExternalConformanceCliRunner` from both `agentos.channels` and top-level
`agentos`.

- [ ] **Step 2: Update docs tests**

Require docs and skill guidance to name the CLI runner, report-path/stdout
import, bounded stdout/stderr evidence, argv-only/no shell parsing, env-key-only
evidence, no certification claim, and deployment-owned CI/certification/live
verification.

- [ ] **Step 3: Update docs and skill guidance**

Document Phase 91 in the roadmap and update production readiness, objective
coverage, and agent-os skill guidance with the runner boundary.

- [ ] **Step 4: Run focused docs/API tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: PASS.

### Task 4: Verification

**Files:**
- No additional edits expected.

- [ ] **Step 1: Run focused phase verification**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\architecture\test_public_api.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run boundary and full verification**

Run:

```powershell
rg -n "A2AExternalConformanceCliRunner|A2AExternalConformanceRunner|external conformance runner" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
uv run python -m compileall -q src tests
git diff --check
uv run pytest -q
```

Expected: runtime boundary scan has no matches, compileall and tests pass, and
diff check has no errors beyond pre-existing line-ending warnings.

