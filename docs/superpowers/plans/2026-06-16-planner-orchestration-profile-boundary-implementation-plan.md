# Planner Orchestration Profile Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deployment-facing planner orchestration readiness profile without implementing a scheduler or planner worker loop in SDK core.

**Architecture:** Add one dataclass in `agentos.multi.planner` next to existing planner primitives. It mirrors the deployment-profile style used for A2A stream lifecycle readiness: JSON-safe metadata, ASGI-compatible readiness checks, required component validation, and explicit SDK-owned vs deployment-owned responsibilities. It does not mutate plans, start workers, call the coordinator, or touch runtime query loops.

**Tech Stack:** Python dataclasses, pytest, existing public API/readiness/docs tests.

---

## File Structure

- Modify `src/agentos/multi/planner.py`: add `PlannerOrchestrationDeploymentProfile`.
- Modify `src/agentos/multi/__init__.py`: export the profile.
- Modify `src/agentos/__init__.py`: export the profile from the top-level package.
- Modify `tests/multi/test_planner_runtime.py`: add behavior tests.
- Modify `tests/architecture/test_public_api.py`: add public API assertions.
- Modify `src/agentos/readiness.py`: add readiness evidence for planner form.
- Modify `tests/test_readiness.py`: assert planner evidence contains the profile while scheduler policy stays app-owned.
- Modify `docs/production-readiness.md`, `.claude/skills/agent-os/modules/agent-forms.md`, and `.claude/skills/agent-os/modules/multi-agent.md`: document the profile.
- Modify `tests/docs/test_production_readiness_docs.py`: lock docs/skill guidance.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 64.

## Task 1: RED Profile Behavior Tests

- [ ] Add `test_planner_orchestration_profile_reports_missing_components` to `tests/multi/test_planner_runtime.py`:

```python
def test_planner_orchestration_profile_reports_missing_components() -> None:
    from agentos.multi.planner import PlannerOrchestrationDeploymentProfile

    profile = PlannerOrchestrationDeploymentProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "PlannerOrchestrationDeploymentProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "decomposition_policy",
        "dag_scheduler",
        "worker_dispatch_loop",
        "compensation_policy",
        "plan_store",
        "worker_supervision",
    }
    assert "PlannerRuntime" in metadata["sdk_owned"]
    assert "production DAG scheduler" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False
```

- [ ] Add `test_planner_orchestration_profile_marks_ready_when_components_are_configured`:

```python
def test_planner_orchestration_profile_marks_ready_when_components_are_configured() -> None:
    from agentos.multi.planner import PlannerOrchestrationDeploymentProfile

    profile = PlannerOrchestrationDeploymentProfile(
        configured_components=(
            "decomposition_policy",
            "dag_scheduler",
            "worker_dispatch_loop",
            "compensation_policy",
            "plan_store",
            "worker_supervision",
        ),
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True
```

- [ ] Add `test_planner_orchestration_profile_rejects_empty_names`:

```python
def test_planner_orchestration_profile_rejects_empty_names() -> None:
    from agentos.multi.planner import PlannerOrchestrationDeploymentProfile

    with pytest.raises(ValueError, match="probe_name"):
        PlannerOrchestrationDeploymentProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerOrchestrationDeploymentProfile(configured_components=("",))
```

- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: fails because `PlannerOrchestrationDeploymentProfile` is not defined.

## Task 2: GREEN Profile Implementation

- [ ] Add constants and `PlannerOrchestrationDeploymentProfile` to `src/agentos/multi/planner.py`.
- [ ] Validate non-empty probe/component names.
- [ ] Implement `missing_components()`, `readiness_metadata()`, and `readiness_check()`.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: planner runtime tests pass.

## Task 3: Public API

- [ ] Export `PlannerOrchestrationDeploymentProfile` from `src/agentos/multi/__init__.py`.
- [ ] Export `PlannerOrchestrationDeploymentProfile` from `src/agentos/__init__.py`.
- [ ] Add assertions in `tests/architecture/test_public_api.py`.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 4: Readiness And Docs

- [ ] Add `PlannerOrchestrationDeploymentProfile` to planner readiness evidence.
- [ ] Add readiness assertions to `tests/test_readiness.py`.
- [ ] Add production docs and skill guidance.
- [ ] Add docs assertions to `tests/docs/test_production_readiness_docs.py`.
- [ ] Append Phase 64 to the roadmap.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: readiness/docs tests pass.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
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
