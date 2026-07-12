# Planner Scheduler Tick Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-shot planner scheduler tick boundary that resets due retries and dispatches dependency-ready steps without embedding a long-running scheduler loop.

**Architecture:** Extend `agentos.multi.planner` with two report dataclasses plus `PlannerRuntime.scheduler_tick(...)`, then expose the primitive through `PlannerTools`, public API exports, readiness docs, skill guidance, objective audit, and the roadmap. The primitive composes existing `retryable_steps()`, `retry_step()`, and `dispatch_ready_steps()` APIs.

**Tech Stack:** Python dataclasses, pytest, existing planner runtime/tool tests, readiness/docs tests.

---

## File Structure

- Modify `src/agentos/multi/planner.py`: add scheduler tick report dataclasses, runtime method, tool registration, tool handler, JSON serializer, and parameter schema.
- Modify `src/agentos/multi/__init__.py`: export the new dataclasses.
- Modify `src/agentos/__init__.py`: export the new dataclasses.
- Modify `tests/multi/test_planner_runtime.py`: add scheduler tick runtime behavior tests.
- Modify `tests/multi/test_planner_tools.py`: add tool registration and JSON handler tests.
- Modify `tests/architecture/test_public_api.py`: add public API assertions.
- Modify `src/agentos/readiness.py`, `docs/production-readiness.md`, `.claude/skills/agent-os/modules/agent-forms.md`, `.claude/skills/agent-os/modules/multi-agent.md`, and `docs/agentos-objective-coverage-audit.md`: document the boundary.
- Modify `tests/test_readiness.py`, `tests/docs/test_production_readiness_docs.py`, and `tests/docs/test_objective_coverage_audit_docs.py`: lock the guidance.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 69.

## Task 1: RED Runtime Tests

- [ ] Add tests for `PlannerRuntime.scheduler_tick(...)` resetting due retryable steps before dispatch.
- [ ] Add tests for retry and dispatch limits.
- [ ] Add tests that no long-running scheduler state is introduced.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: fails because scheduler tick types/methods are not defined.

## Task 2: GREEN Runtime Implementation

- [ ] Add `PlanSchedulerRetryReset`.
- [ ] Add `PlanSchedulerTickReport`.
- [ ] Implement `PlannerRuntime.scheduler_tick(...)`.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: planner runtime tests pass.

## Task 3: Planner Tool Boundary

- [ ] Add `plan_scheduler_tick` registration.
- [ ] Add tool handler and JSON serializer.
- [ ] Add parameter schema with `plan_id`, `default_template_id`,
  `retry_limit`, and `dispatch_limit`.
- [ ] Add tests for registration order, owner scoping, and JSON report shape.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_tools.py -q
```

Expected: planner tool tests pass.

## Task 4: Public API

- [ ] Export scheduler tick report dataclasses from `agentos.multi`.
- [ ] Export scheduler tick report dataclasses from top-level `agentos`.
- [ ] Add public API assertions.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 5: Readiness, Docs, Skill, And Roadmap

- [ ] Add scheduler tick evidence to planner readiness.
- [ ] Update production readiness and agent-os skill guidance.
- [ ] Update objective coverage audit and roadmap Phase 69.
- [ ] Add docs/readiness tests.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: readiness/docs tests pass.

## Task 6: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
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
