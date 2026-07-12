# Worker Process Lifecycle Profile Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deployment-facing worker process lifecycle readiness profile without implementing a process supervisor in SDK core.

**Architecture:** Add one dataclass in `agentos.runtime.profile` next to existing runtime deployment profiles. It mirrors the readiness-profile pattern already used by distributed web sessions, workspace isolation, A2A stream lifecycle, and planner orchestration: JSON-safe metadata, ASGI-compatible readiness checks, required component validation, and explicit SDK-owned vs deployment-owned responsibilities. It does not start, restart, stop, scale, lock, credential, migrate, or monitor real worker processes.

**Tech Stack:** Python dataclasses, pytest, existing public API/readiness/docs tests.

---

## File Structure

- Modify `src/agentos/runtime/profile.py`: add `WorkerProcessLifecycleDeploymentProfile`.
- Modify `src/agentos/runtime/__init__.py`: export the profile.
- Modify `src/agentos/__init__.py`: export the profile from the top-level package.
- Modify `tests/runtime/test_runtime_profile.py`: add behavior tests.
- Modify `tests/architecture/test_public_api.py`: add public API assertions.
- Modify `src/agentos/readiness.py`: add readiness evidence for team and planner forms.
- Modify `tests/test_readiness.py`: assert team/planner evidence contains the profile.
- Modify `docs/production-readiness.md`, `.claude/skills/agent-os/modules/agent-forms.md`, and `.claude/skills/agent-os/modules/multi-agent.md`: document the profile.
- Modify `tests/docs/test_production_readiness_docs.py` and `tests/docs/test_objective_coverage_audit_docs.py`: lock docs/audit guidance.
- Modify `docs/agentos-objective-coverage-audit.md`: update remaining blockers.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 70.

## Task 1: RED Profile Behavior Tests

- [ ] Add tests for `WorkerProcessLifecycleDeploymentProfile` in `tests/runtime/test_runtime_profile.py`.
- [ ] Run:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py::test_worker_process_lifecycle_deployment_profile_reports_missing_components tests\runtime\test_runtime_profile.py::test_worker_process_lifecycle_deployment_profile_marks_ready_when_components_are_configured tests\runtime\test_runtime_profile.py::test_worker_process_lifecycle_deployment_profile_rejects_empty_names -q
```

Expected: fails because `WorkerProcessLifecycleDeploymentProfile` is not defined.

## Task 2: GREEN Profile Implementation

- [ ] Add required components and `WorkerProcessLifecycleDeploymentProfile` to `src/agentos/runtime/profile.py`.
- [ ] Validate non-empty probe/component names.
- [ ] Implement `missing_components()`, `readiness_metadata()`, and `readiness_check()`.
- [ ] Run:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py -q
```

Expected: runtime profile tests pass.

## Task 3: Public API

- [ ] Export `WorkerProcessLifecycleDeploymentProfile` from `src/agentos/runtime/__init__.py`.
- [ ] Export `WorkerProcessLifecycleDeploymentProfile` from `src/agentos/__init__.py`.
- [ ] Add assertions in `tests/architecture/test_public_api.py`.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 4: Readiness And Docs

- [ ] Add `WorkerProcessLifecycleDeploymentProfile` to team/planner readiness evidence.
- [ ] Add readiness assertions to `tests/test_readiness.py`.
- [ ] Add production docs and skill guidance.
- [ ] Add docs assertions to `tests/docs/test_production_readiness_docs.py`.
- [ ] Add objective audit assertions to `tests/docs/test_objective_coverage_audit_docs.py`.
- [ ] Append Phase 70 to the roadmap.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: readiness/docs tests pass.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
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
