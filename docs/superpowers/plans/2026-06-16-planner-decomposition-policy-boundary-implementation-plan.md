# Planner Decomposition Policy Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned validation/report boundary and deployment-facing readiness profile for LLM-produced planner decompositions without implementing automatic LLM planning.

**Architecture:** Add one validation report dataclass, one `PlannerRuntime.validate_decomposition(...)` method, and one deployment profile in `agentos.multi.planner`. The validation method reuses existing decomposition-to-step and dependency checks but catches structural errors into JSON-safe report data and never writes to `PlanStore`. The profile mirrors existing readiness-profile patterns and keeps prompts, model routing, approvals, evals, rollout, and live backend verification deployment-owned.

**Tech Stack:** Python dataclasses, pytest, existing public API/readiness/docs tests.

---

## File Structure

- Modify `src/agentos/multi/planner.py`: add `PlanDecompositionValidationReport`, `PlannerRuntime.validate_decomposition(...)`, and `PlannerDecompositionPolicyDeploymentProfile`.
- Modify `src/agentos/multi/__init__.py`: export the new report/profile.
- Modify `src/agentos/__init__.py`: export the new report/profile from the top-level package.
- Modify `tests/multi/test_planner_runtime.py`: add behavior tests for validation and profile readiness.
- Modify `tests/architecture/test_public_api.py`: add public API assertions.
- Modify `src/agentos/readiness.py`: add planner readiness evidence for the validation/profile boundary.
- Modify `tests/test_readiness.py`: assert planner evidence contains the new boundary and still keeps automatic LLM policy deployment-owned.
- Modify `docs/production-readiness.md`, `.claude/skills/agent-os/SKILL.md`, `.claude/skills/agent-os/flow/01-requirements.md`, `.claude/skills/agent-os/flow/02-spec-generation.md`, `.claude/skills/agent-os/modules/agent-forms.md`, and `.claude/skills/agent-os/modules/multi-agent.md`: document the boundary.
- Modify `tests/docs/test_production_readiness_docs.py` and `tests/docs/test_objective_coverage_audit_docs.py`: lock docs/audit guidance.
- Modify `docs/agentos-objective-coverage-audit.md`: update planner and intent-router coverage.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 71.

## Task 1: RED Validation Behavior Tests

- [ ] Add tests in `tests/multi/test_planner_runtime.py`:

```python
def test_planner_runtime_validates_decomposition_without_creating_plan() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
        ),
        id_factory=lambda prefix: f"{prefix}_1",
    )

    report = runtime.validate_decomposition(
        PlanDecomposition(
            objective="Review planner decomposition policy.",
            steps=(
                PlanStepSpec(
                    instruction="Collect evidence.",
                    step_id="collect",
                    template_id="researcher",
                ),
                PlanStepSpec(
                    instruction="Write synthesis.",
                    step_id="write",
                    depends_on=("collect",),
                ),
            ),
        ),
    )

    assert report.ok is True
    assert report.errors == ()
    assert report.step_count == 2
    assert report.required_templates == ("researcher",)
    assert report.unknown_templates == ()
    assert report.as_dict()["ok"] is True
    assert runtime.list_plans() == []
```

- [ ] Add invalid decomposition report test:

```python
def test_planner_runtime_validation_reports_decomposition_errors() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="known",
                name="Known",
                role="Known worker.",
            ),
        ),
    )

    report = runtime.validate_decomposition(
        PlanDecomposition(
            objective="Invalid plan.",
            steps=(
                PlanStepSpec(
                    instruction="Use missing template.",
                    step_id="first",
                    template_id="missing",
                ),
                PlanStepSpec(
                    instruction="Duplicate id.",
                    step_id="first",
                    depends_on=("unknown",),
                ),
            ),
        ),
    )

    assert report.ok is False
    assert report.step_count == 2
    assert report.required_templates == ("missing",)
    assert report.unknown_templates == ("missing",)
    assert any("unknown template" in error for error in report.errors)
    assert runtime.list_plans() == []
```

- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py::test_planner_runtime_validates_decomposition_without_creating_plan tests\multi\test_planner_runtime.py::test_planner_runtime_validation_reports_decomposition_errors -q
```

Expected: fails because `PlannerRuntime.validate_decomposition` is not defined.

## Task 2: GREEN Validation Implementation

- [ ] Add `PlanDecompositionValidationReport` to `src/agentos/multi/planner.py`.
- [ ] Add `PlannerRuntime.validate_decomposition(...)`.
- [ ] Reuse `_step_from_spec(...)` and `_validate_step_dependencies(...)` inside the validation method, but catch `ValueError` and `KeyError` into report errors.
- [ ] Ensure validation does not call `store.create_plan(...)` or `store.save_plan(...)`.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: planner runtime tests pass.

## Task 3: RED/GREEN Profile Behavior

- [ ] Add tests for `PlannerDecompositionPolicyDeploymentProfile` in `tests/multi/test_planner_runtime.py`.
- [ ] Run the three profile tests and verify they fail because the profile is not defined.
- [ ] Add required components and `PlannerDecompositionPolicyDeploymentProfile` to `src/agentos/multi/planner.py`.
- [ ] Validate non-empty probe/component names.
- [ ] Implement `missing_components()`, `readiness_metadata()`, and `readiness_check()`.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: planner runtime tests pass.

## Task 4: Public API

- [ ] Export `PlanDecompositionValidationReport` and `PlannerDecompositionPolicyDeploymentProfile` from `src/agentos/multi/__init__.py`.
- [ ] Export both from `src/agentos/__init__.py`.
- [ ] Add assertions in `tests/architecture/test_public_api.py`.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 5: Readiness And Docs

- [ ] Add the new report/profile to planner readiness evidence.
- [ ] Keep automatic LLM prompt/model/approval/eval policy in required app glue.
- [ ] Add readiness assertions to `tests/test_readiness.py`.
- [ ] Add production docs and skill guidance.
- [ ] Add docs/audit assertions.
- [ ] Append Phase 71 to the roadmap.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: readiness/docs tests pass.

## Task 6: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
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
