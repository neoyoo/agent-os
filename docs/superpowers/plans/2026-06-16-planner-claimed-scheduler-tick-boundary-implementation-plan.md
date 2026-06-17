# Planner Claimed Scheduler Tick Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned helper and tool that claim schedulable planner plans before running one-shot scheduler ticks.

**Architecture:** Extend `agentos.multi.planner` with JSON-safe claimed-tick report dataclasses and `PlannerRuntime.claimed_scheduler_tick(...)`. Expose the same boundary through `PlannerTools.plan_claimed_scheduler_tick`, owner-scoped by the tool instance, and keep operational scheduler policy deployment-owned.

**Tech Stack:** Python dataclasses, existing planner runtime/tool APIs, pytest, docs/readiness tests.

---

## File Structure

- Modify `src/agentos/multi/planner.py`: add report dataclasses, runtime method, tool registration, handler, serializer, and schema.
- Modify `src/agentos/multi/__init__.py`: export new dataclasses.
- Modify `src/agentos/__init__.py`: export new dataclasses.
- Modify `tests/multi/test_planner_runtime.py`: add claimed scheduler tick runtime tests.
- Modify `tests/multi/test_planner_tools.py`: add tool registration and handler tests.
- Modify `tests/architecture/test_public_api.py`: add public API assertions.
- Modify `src/agentos/readiness.py`, `docs/production-readiness.md`, `docs/agentos-objective-coverage-audit.md`, `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`, `.claude/skills/agent-os/SKILL.md`, `.claude/skills/agent-os/modules/agent-forms.md`, and `.claude/skills/agent-os/modules/multi-agent.md`: document the boundary.
- Modify docs tests to require the new evidence.

## Task 1: RED Runtime Tests

- [ ] Add a runtime test proving a worker ticks only the plan it successfully
      claims while a busy plan is skipped.
- [ ] Add a runtime test proving `release_after_tick=True` releases only the
      current worker's claim and lets a later worker claim/tick.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: fail because `PlannerRuntime.claimed_scheduler_tick` and the report
dataclasses are not defined.

## Task 2: GREEN Runtime Implementation

- [ ] Add `PlanClaimedSchedulerTickSkip`.
- [ ] Add `PlanClaimedSchedulerTickReport`.
- [ ] Implement `PlannerRuntime.claimed_scheduler_tick(...)` with claim-store
      validation, owner/status/limit filters, retry/dispatch limits, and
      optional release.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: planner runtime tests pass.

## Task 3: Planner Tool Boundary

- [ ] Register `plan_claimed_scheduler_tick`.
- [ ] Add the handler, JSON serializer, and parameter schema.
- [ ] Add tests for registration order, owner scoping, busy skip JSON,
      `release_after_tick`, and validation.
- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_tools.py -q
```

Expected: planner tool tests pass.

## Task 4: Public API And Guidance

- [ ] Export the new report dataclasses from `agentos.multi` and top-level
      `agentos`.
- [ ] Update readiness/docs/skill/audit/roadmap language.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: public API and docs tests pass.

## Task 5: Verification

- [ ] Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\multi\test_planner_scheduler_daemon.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

- [ ] Run:

```powershell
uv run pytest -q
```

- [ ] Run:

```powershell
uv run python -m compileall -q src tests
```

- [ ] Run:

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlanClaim|PostgresPlanClaimStore|PlanClaimedSchedulerTick" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no output.

- [ ] Run:

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if the command exits 0.
