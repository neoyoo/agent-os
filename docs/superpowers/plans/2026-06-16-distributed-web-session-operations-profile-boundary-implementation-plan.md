# Distributed Web Session Operations Profile Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deployment-facing distributed web session operations readiness profile without implementing migration runners, credential managers, stale lease sweepers, or recovery loops in SDK core.

**Architecture:** Add one dataclass in `agentos.runtime.profile`. It returns JSON-safe readiness metadata and ASGI-compatible readiness checks that show which distributed session operations components are configured. It complements `DistributedWebRuntimeProfile` and does not change turn execution or query loops.

**Tech Stack:** Python dataclasses, pytest, existing runtime profile, public API, readiness, and docs tests.

---

## File Structure

- Modify `src/agentos/runtime/profile.py`: add `DistributedWebSessionOperationsProfile`.
- Modify `src/agentos/runtime/__init__.py`: export the profile.
- Modify `src/agentos/__init__.py`: export the profile.
- Modify `tests/runtime/test_runtime_profile.py`: add behavior tests.
- Modify `tests/architecture/test_public_api.py`: add public API assertions.
- Modify `src/agentos/readiness.py`: add evidence for distributed web operations readiness.
- Modify `tests/test_readiness.py`: assert evidence and deployment-owned gaps.
- Modify `docs/production-readiness.md`, `.claude/skills/agent-os/modules/agent-forms.md`, `.claude/skills/agent-os/modules/architecture.md`, and `.claude/skills/agent-os/modules/persistence.md`: document the profile.
- Modify `tests/docs/test_production_readiness_docs.py`: lock docs/skill guidance.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 66.

## Task 1: RED Profile Behavior Tests

- [ ] Add `test_distributed_web_session_operations_profile_reports_missing_components` to `tests/runtime/test_runtime_profile.py`.
- [ ] Add `test_distributed_web_session_operations_profile_marks_ready_when_components_are_configured`.
- [ ] Add `test_distributed_web_session_operations_profile_rejects_empty_names`.
- [ ] Run:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py -q
```

Expected: fails because `DistributedWebSessionOperationsProfile` is not defined.

## Task 2: GREEN Profile Implementation

- [ ] Add required components and `DistributedWebSessionOperationsProfile` to `src/agentos/runtime/profile.py`.
- [ ] Validate non-empty probe/component names.
- [ ] Implement `missing_components()`, `readiness_metadata()`, and `readiness_check()`.
- [ ] Ensure `DistributedWebRuntimeProfile.readiness_metadata()` names `DistributedWebSessionOperationsProfile` as its operational readiness profile.
- [ ] Run:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py -q
```

Expected: runtime profile tests pass.

## Task 3: Public API

- [ ] Export `DistributedWebSessionOperationsProfile` from `src/agentos/runtime/__init__.py`.
- [ ] Export it from `src/agentos/__init__.py`.
- [ ] Add assertions in `tests/architecture/test_public_api.py`.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 4: Readiness And Docs

- [ ] Add `DistributedWebSessionOperationsProfile` to distributed web readiness evidence.
- [ ] Add readiness assertions to `tests/test_readiness.py`.
- [ ] Add production docs and skill guidance.
- [ ] Add docs assertions to `tests/docs/test_production_readiness_docs.py`.
- [ ] Append Phase 66 to the roadmap.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: readiness/docs tests pass.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
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
