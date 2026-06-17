# Planner Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 6B planner tools so LLM-facing agents can create, update, assign, complete, and inspect planner state through normal ToolRegistry external tools.

**Architecture:** Add `PlannerTools` next to `PlannerRuntime` in `agentos.multi.planner`. It wraps `PlannerRuntime`, serializes plan state to deterministic JSON, and registers six `RegisteredTool` handlers. The runtime loops remain unaware of planner concepts.

**Tech Stack:** Python 3.11 dataclasses, JSON serialization, existing `ToolRegistry` / `RegisteredTool`, pytest.

---

## Scope Contract

Implement only the tools described by `docs/superpowers/specs/2026-06-12-planner-tools-design.md`.

Target conclusion:

```text
PlannerTools make planner primitives usable by tool-calling agents while keeping
PlannerRuntime as the state boundary and QueryLoop as a single-agent turn loop.
```

Deferred:

- LLM automatic decomposition.
- DAG scheduling.
- Persistent database-backed `PlanStore`.
- Production retry/scheduling policy.
- UI plan stream.

Production constraints:

- Planner tools are scoped to the bound `owner_agent_id`; tool callers cannot
  inspect or mutate another owner's plan by guessing `plan_id`.
- Evidence kind is a closed enum in both runtime validation and tool schema.

## File Structure

Modify:

- `src/agentos/multi/planner.py`
  Add `PlannerTools` and JSON projection helpers.

- `src/agentos/multi/__init__.py`
  Export `PlannerTools`.

- `src/agentos/__init__.py`
  Mirror `PlannerTools` at top level.

- `tests/multi/test_planner_tools.py`
  New tests for registration and handlers.

- `tests/architecture/test_public_api.py`
  Add public API assertions.

- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/multi-agent.md`
- `.claude/skills/agent-os/flow/01-requirements.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
- `.claude/skills/agent-os/SKILL.md`
  Update planner readiness language.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- `src/agentos/context/state.py`

## Task 1: Planner Tool Registration

**Files:**
- Create: `tests/multi/test_planner_tools.py`
- Modify: `src/agentos/multi/planner.py`

- [x] Write failing test that `PlannerTools.register()` registers exactly:
  `plan_create`, `plan_add_step`, `plan_assign_step`, `plan_record_evidence`,
  `plan_complete_step`, `plan_status`.
- [x] Run the test and confirm it fails because `PlannerTools` is missing.
- [x] Implement `PlannerTools.__init__` and `register()`.
- [x] Run the registration test and confirm it passes.

## Task 2: Plan Create/Add/Status Handlers

**Files:**
- Modify: `tests/multi/test_planner_tools.py`
- Modify: `src/agentos/multi/planner.py`

- [x] Write failing tests for `plan_create`, `plan_add_step`, and `plan_status`.
- [x] Run tests and confirm handlers are missing or incomplete.
- [x] Implement JSON projection helpers and the three handlers.
- [x] Run targeted tests and confirm they pass.

## Task 3: Assign/Evidence/Complete Handlers

**Files:**
- Modify: `tests/multi/test_planner_tools.py`
- Modify: `src/agentos/multi/planner.py`

- [x] Write failing tests for `plan_assign_step`, `plan_record_evidence`, and
  `plan_complete_step`.
- [x] Run tests and confirm expected failures.
- [x] Implement handlers by delegating to `PlannerRuntime`.
- [x] Run targeted tests and confirm they pass.

## Task 4: Public API And Skill Docs

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify listed `.claude/skills/agent-os` docs.

- [x] Write failing public API assertion for `PlannerTools`.
- [x] Export `PlannerTools`.
- [x] Update skill docs so planner tools are primitives-ready, while automatic
  decomposition, DAG scheduling, persistent plan store, and retry policy remain
  future work for this historical Phase 6B slice. Later phases add
  `PostgresPlanStore`; Phase 39 adds `PlanRetryPolicy` and step
  fail/retry tools.
- [x] Run public API test and docs drift search.

## Task 5: Verification

- [x] Run `uv run pytest tests/multi/test_planner_tools.py tests/multi/test_planner_runtime.py tests/multi/test_coordinator_dispatch.py tests/architecture/test_public_api.py -q`.
- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Run runtime boundary search:

```powershell
rg "agentos.multi.planner|PlannerTools|PlannerRuntime|PlanState|SubAgentTemplate|AgentCoordinator|Redis|Postgres|A2A|workspace" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

## Self-Review

- Spec coverage: registration, create/add/status, assign/evidence/complete,
  owner-scoped tool access, evidence kind enum validation, public exports, docs,
  and verification are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: no runtime loop changes.
