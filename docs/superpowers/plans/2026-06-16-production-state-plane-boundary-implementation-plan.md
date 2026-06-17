# Production State Plane Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production state-plane readiness profile that separates registry, queue, truth store, worker lifecycle, and session snapshot responsibilities.

**Architecture:** Reuse the existing deployment-profile pattern in `src/agentos/runtime/profile.py`: required components, configured components, missing component detection, JSON-safe metadata, and readiness checks. Keep this as readiness metadata only; concrete Nacos, Redis, Postgres, supervisor, and sandbox adapters remain later phases.

**Tech Stack:** Python dataclasses, pytest, existing `agentos.runtime.profile` exports, markdown production readiness docs, AgentOS skill guidance.

---

### Task 1: Runtime Profile Contract

**Files:**
- Modify: `src/agentos/runtime/profile.py`
- Modify: `src/agentos/runtime/__init__.py`
- Modify: `src/agentos/__init__.py`
- Test: `tests/runtime/test_runtime_profile.py`
- Test: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing runtime profile tests**

Add tests that import `ProductionStatePlaneDeploymentProfile`, assert missing
component reporting, assert a ready profile when all required components are
configured, assert state-plane responsibility metadata, and assert empty
component validation.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py::test_production_state_plane_deployment_profile_reports_missing_components tests\runtime\test_runtime_profile.py::test_production_state_plane_deployment_profile_marks_ready_when_components_are_configured tests\runtime\test_runtime_profile.py::test_production_state_plane_deployment_profile_rejects_empty_names -q
```

Expected: fail because `ProductionStatePlaneDeploymentProfile` is not defined.

- [ ] **Step 3: Implement the profile**

Add `PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS` and
`ProductionStatePlaneDeploymentProfile` to `src/agentos/runtime/profile.py`.
The metadata must include state-plane responsibility entries for registry,
message queue, task store, plan store, worker process supervisor, and session
snapshot persistence.

- [ ] **Step 4: Export the profile**

Export `ProductionStatePlaneDeploymentProfile` from `agentos.runtime` and
top-level `agentos`, then update public API tests.

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py tests\architecture\test_public_api.py -q
```

Expected: pass.

### Task 2: Readiness And Documentation

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/architecture.md`
- Modify: `.claude/skills/agent-os/modules/persistence.md`
- Test: `tests/test_readiness.py`
- Test: `tests/docs/test_production_readiness_docs.py`
- Test: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Write failing documentation/readiness tests**

Add assertions that `ProductionStatePlaneDeploymentProfile` appears in
distributed web, team, and planner readiness evidence and that production docs
and skill guidance describe the state-plane split.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: fail because docs and readiness evidence do not yet mention the new
profile.

- [ ] **Step 3: Update readiness and docs**

Add the profile to relevant form evidence, document the state-plane split, and
append Phase 86 to the roadmap.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: pass.

### Task 3: Verification

**Files:**
- Verify: `src/agentos/runtime/query_loop.py`
- Verify: `src/agentos/runtime/async_query_loop.py`

- [ ] **Step 1: Run focused verification**

Run:

```powershell
uv run pytest tests\runtime\test_runtime_profile.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

- [ ] **Step 2: Run boundary scan**

Run:

```powershell
rg "ProductionStatePlane|WorkerProcessSupervisor|NacosAgentRegistry|RedisAgentMessageQueue|PostgresTaskStore|PostgresPlanStore" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code `1`, no output.

- [ ] **Step 3: Run full verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q src tests
git diff --check
```

Expected: tests and compile pass; `git diff --check` has no whitespace errors.
