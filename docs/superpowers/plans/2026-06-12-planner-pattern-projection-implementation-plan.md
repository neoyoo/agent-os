# Planner Pattern Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 6C planner pattern projection helpers and examples so intent-router and plan-and-execute agents have a standard SDK usage path.

**Architecture:** Keep `PlannerRuntime` as the truth source and add a pure projection helper in `agentos.multi.planner`. Add one example module under `src/agentos/examples` that composes `PlannerRuntime`, `PlannerTools`, `SubAgentTemplate`, and `AgentCoordinator` without touching `QueryLoop`.

**Tech Stack:** Python 3.11 dataclasses, existing planner/coordinator primitives, pytest.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-planner-pattern-projection-design.md`.

Target conclusion:

```text
PlannerRuntime is the plan truth source; working state receives only compact
summary projections for model awareness.
```

Deferred:

- Automatic LLM decomposition.
- DAG scheduling.
- Persistent database-backed `PlanStore`.
- Production retry/scheduling policy.
- Automatic context runtime writes.

## File Structure

Modify:

- `src/agentos/multi/planner.py`
  Add `plan_to_working_state_summary`.

- `src/agentos/multi/__init__.py`
  Export `plan_to_working_state_summary`.

- `src/agentos/__init__.py`
  Mirror `plan_to_working_state_summary` at top level.

- `tests/multi/test_planner_projection.py`
  New tests for projection shape and redaction.

- `src/agentos/examples/planner_patterns.py`
  New examples for intent-router and plan-and-execute.

- `tests/examples/test_planner_patterns.py`
  New tests for examples.

- `tests/architecture/test_public_api.py`
  Add public API assertions.

- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/multi-agent.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
  Clarify working-state projection boundary.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- `src/agentos/context/state.py`

## Task 1: Projection Helper

**Files:**
- Create: `tests/multi/test_planner_projection.py`
- Modify: `src/agentos/multi/planner.py`

- [x] Write failing tests that `plan_to_working_state_summary(plan)` returns
  deterministic counts, next steps, and recent evidence.
- [x] Write failing tests that the projection omits workspace roots, task ids,
  evidence URI/metadata, timestamps, and owner identity.
- [x] Implement the pure projection helper.
- [x] Run `uv run pytest tests/multi/test_planner_projection.py -q`.

## Task 2: Public API

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [x] Write failing public API assertions for `plan_to_working_state_summary`.
- [x] Export from `agentos.multi` and top-level `agentos`.
- [x] Run `uv run pytest tests/architecture/test_public_api.py -q`.

## Task 3: Planner Pattern Examples

**Files:**
- Create: `src/agentos/examples/planner_patterns.py`
- Create: `tests/examples/test_planner_patterns.py`
- Modify: `tests/docs/test_production_hardening_docs.py`

- [x] Write failing tests for `build_intent_router_example()` and
  `build_plan_and_execute_example()`.
- [x] Write failing test that the example has a `main()` entrypoint.
- [x] Implement examples using planner/coordinator primitives.
- [x] Run `uv run pytest tests/examples/test_planner_patterns.py tests/docs/test_production_hardening_docs.py -q`.

## Task 4: Skill Docs

**Files:**
- Modify listed `.claude/skills/agent-os` docs.

- [x] Update docs to say plan state belongs in `PlanStore`, not working state.
- [x] Update docs to mention `plan_to_working_state_summary`.
- [x] Run docs drift search for stale "project summaries into working state"
  wording that does not mention projection/truth-source boundaries.

## Task 5: Verification

- [x] Run `uv run pytest tests/multi/test_planner_projection.py tests/multi/test_planner_tools.py tests/multi/test_planner_runtime.py tests/examples/test_planner_patterns.py tests/architecture/test_public_api.py -q`.
- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Run runtime boundary search:

```powershell
rg "agentos.multi.planner|PlannerTools|PlannerRuntime|PlanState|SubAgentTemplate|plan_to_working_state_summary|AgentCoordinator|Redis|Postgres|A2A|workspace" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

## Self-Review

- Spec coverage: projection helper, redaction, examples, public exports, docs,
  and verification are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: no runtime loop changes.
