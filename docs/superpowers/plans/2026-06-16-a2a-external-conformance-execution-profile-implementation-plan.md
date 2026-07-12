# A2A External Conformance Execution Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deployment-facing A2A external conformance execution profile that turns imported external reports into readiness metadata without embedding an external runner or claiming certification.

**Architecture:** Extend `agentos.channels.a2a_conformance` with one dataclass that consumes `A2AConformanceReport`, required check ids, and configured deployment components. Export it publicly, reference it from readiness/docs/skill, and append Phase 68 to the roadmap.

**Tech Stack:** Python dataclasses, pytest, existing A2A conformance report model, existing readiness/docs tests.

---

## File Structure

- Modify `src/agentos/channels/a2a_conformance.py`: add `A2AExternalConformanceExecutionProfile` and required-component defaults.
- Modify `src/agentos/channels/__init__.py`: export the profile.
- Modify `src/agentos/__init__.py`: export the profile.
- Modify `tests/channels/test_a2a_conformance.py`: add profile behavior tests.
- Modify `tests/architecture/test_public_api.py`: add public API assertions.
- Modify `src/agentos/readiness.py`: add profile evidence and update external conformance gap.
- Modify `tests/test_readiness.py`: assert evidence and remaining deployment-owned work.
- Modify `docs/production-readiness.md`, `.claude/skills/agent-os/modules/agent-forms.md`, `.claude/skills/agent-os/modules/multi-agent.md`, and `docs/agentos-objective-coverage-audit.md`: document the boundary.
- Modify `tests/docs/test_production_readiness_docs.py` and `tests/docs/test_objective_coverage_audit_docs.py`: lock docs guidance.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 68.

## Task 1: RED Profile Behavior Tests

- [ ] Add tests for missing report, missing deployment components, failed required checks, ready metadata, and input validation.
- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: fails because `A2AExternalConformanceExecutionProfile` is not defined.

## Task 2: GREEN Profile Implementation

- [ ] Add default required component and required check constants.
- [ ] Add `A2AExternalConformanceExecutionProfile`.
- [ ] Implement `missing_components()`, `missing_required_checks()`,
  `failed_required_checks()`, `readiness_metadata()`, and `readiness_check()`.
- [ ] Validate non-empty probe name, component names, and required check ids.
- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: A2A conformance tests pass.

## Task 3: Public API

- [ ] Export the profile from `agentos.channels`.
- [ ] Export the profile from top-level `agentos`.
- [ ] Add public API assertions.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 4: Readiness And Docs

- [ ] Add profile evidence to the A2A readiness matrix.
- [ ] Update readiness assertions.
- [ ] Update production docs, agent-os skill docs, objective audit, and roadmap.
- [ ] Update docs tests.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: readiness/docs tests pass.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
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

