# Planner Pattern Projection Design (Phase 6C)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 6A planner primitives and Phase 6B planner tools

## Target Conclusion

```text
PlannerRuntime is the truth source for plan state.
Working state may carry a compact plan summary for model awareness, but it must
not become the plan store or a second source of truth.
```

Phase 6A made planner state and subagent templates available to app code. Phase
6B made those operations available to tool-calling agents. Phase 6C should make
the intended application patterns concrete: intent-router and plan-and-execute
agents can be built with the SDK without changing QueryLoop.

## Scope

Add a small projection helper and one SDK example module:

- `plan_to_working_state_summary(plan)`
- `src/agentos/examples/planner_patterns.py`

The helper returns a JSON-safe, prompt-safe summary that can be written through
normal context protocol tools when an agent needs plan awareness. It is a
projection only; it does not mutate `ContextState`, `WorkingState`,
`PlannerRuntime`, or `PlanStore`.

The example module demonstrates:

- intent-router: choose a `SubAgentTemplate`, create a plan, add one routed
  step, and return a working-state summary.
- plan-and-execute: create multiple steps, assign a step through a coordinator,
  record evidence, complete the step, and return a working-state summary.

## Projection Shape

`plan_to_working_state_summary(plan)` returns:

- `plan_id`
- `objective`
- `status`
- `step_counts`
- `next_steps`
- `recent_evidence`

`next_steps` includes stable fields the model needs for follow-up tool calls:

- `step_id`
- `instruction`
- `status`
- `template_id`
- `assigned_agent_id`
- `required_capabilities`
- `evidence_ids`

The projection intentionally omits:

- workspace handles and local paths
- task ids and transport details
- evidence URI and metadata
- timestamps
- owner identity

## Non-Goals

- No automatic decomposition.
- No DAG scheduler.
- No persistent database-backed `PlanStore`.
- No retry/scheduling policy.
- No QueryLoop or AsyncQueryLoop integration.
- No automatic writing into working state.

## Acceptance Criteria

- Projection helper returns deterministic JSON-safe values.
- Projection helper omits workspace roots, task ids, evidence URIs, evidence
  metadata, and timestamps.
- Projection helper can limit visible steps without losing aggregate counts.
- Intent-router example uses planner primitives without custom runtime changes.
- Plan-and-execute example assigns a step, records evidence, completes it, and
  returns a summary projection.
- Public exports are available from `agentos.multi` and top-level `agentos`.
- Skill docs explain that working state receives only summary projections.
- `QueryLoop` and `AsyncQueryLoop` remain free of planner imports.
