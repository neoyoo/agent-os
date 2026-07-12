# Planner Tools Design (Phase 6B)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Status: design for execution slice
> Builds on: `docs/superpowers/specs/2026-06-12-planner-subagent-template-design.md`

## Target Conclusion

```text
Planner state should be usable by an LLM agent through explicit tools.
PlannerTools wrap PlannerRuntime and expose safe plan operations without adding
planner branches to QueryLoop or turning working state into the truth source.
```

Phase 6A made planner primitives available to application code. Phase 6B should
make those primitives usable from a normal tool-calling agent so a main agent can
create a plan, add steps, assign a step to a subagent template, record evidence,
complete a step, and inspect current plan status.

## Scope

Add a small `PlannerTools` wrapper that registers external tools into
`ToolRegistry`, following the existing `AgentCoordinationTools` style:

- `plan_create`
- `plan_add_step`
- `plan_assign_step`
- `plan_record_evidence`
- `plan_complete_step`
- `plan_status`

Each handler delegates to `PlannerRuntime`, returns deterministic JSON, and does
not mutate `ContextState`, `MessageRuntime`, `QueryLoop`, or `AsyncQueryLoop`.
The tool wrapper is scoped to its bound `owner_agent_id`: a caller can only
inspect or mutate plans owned by that agent.

## Tool Semantics

`plan_create`:

- Input: `objective`, optional `plan_id`.
- Uses the bound `owner_agent_id`.
- Returns a plan summary with `plan_id`, `objective`, `status`, `steps`,
  `evidence`, and `assignments`.

`plan_add_step`:

- Input: `plan_id`, `instruction`, optional `required_capabilities`, optional
  `template_id`.
- Returns the updated plan summary.

`plan_assign_step`:

- Input: `plan_id`, `step_id`, `template_id`.
- Calls `PlannerRuntime.assign_step`.
- Returns updated plan summary including assignment/task ids.

`plan_record_evidence`:

- Input: `plan_id`, optional `step_ids`, `kind`, `summary`, optional `uri`,
  optional `producer_agent_id`, optional string metadata.
- `kind` is limited to `text`, `artifact`, `task_result`, `team_message`, or
  `external`.
- Returns the created evidence handle.

`plan_complete_step`:

- Input: `plan_id`, `step_id`, optional `evidence_ids`.
- Returns the updated plan summary.

`plan_status`:

- Input: optional `plan_id`.
- If `plan_id` is present, returns that plan summary.
- Otherwise returns all plans for the bound owner agent.

## JSON Projection

Planner tools should use JSON-safe projections rather than leaking dataclass
objects directly. The projection should include:

- plan: `plan_id`, `objective`, `owner_agent_id`, `status`, `created_at`,
  `updated_at`
- steps: `step_id`, `instruction`, `status`, `required_capabilities`,
  `assigned_agent_id`, `template_id`, `task_id`, `evidence_ids`, `error`
- assignments: `plan_id`, `step_id`, `template_id`, `task_id`,
  `target_agent_id`, `created_at`
- evidence: `evidence_id`, `kind`, `summary`, `uri`, `producer_agent_id`,
  `metadata`

Workspace handles should be represented only as stable metadata if needed later;
Phase 6B can omit workspace from the tool response to avoid exposing local paths.

## Non-Goals

- No automatic LLM decomposition.
- No graph/DAG scheduler.
- No persistent database-backed `PlanStore`.
- No direct QueryLoop/AsyncQueryLoop integration.
- No automatic projection into working state.
- No UI plan stream.

## Acceptance Criteria

- `PlannerTools.register()` registers six external tools with stable names and
  JSON schemas.
- Tool handlers call `PlannerRuntime` and return deterministic JSON.
- Tool handlers enforce owner-scoped access before reading or mutating plan
  state.
- `plan_assign_step` works with both spawn templates and persistent expert
  templates.
- `plan_status` can return one plan or owner-scoped plan list.
- `plan_record_evidence` rejects unsupported evidence kinds and publishes the
  allowed kinds in its JSON schema.
- Public exports are available from `agentos.multi` and top-level `agentos`.
- Skill docs move planner tools from future work to primitives-ready while still
  identifying automatic decomposition, DAG scheduling, persistent `PlanStore`,
  and retry/scheduling policy as future work for this historical Phase 6B
  slice. Later phases add `PostgresPlanStore`; Phase 39 adds `PlanRetryPolicy`
  and step fail/retry tools.
- `QueryLoop` and `AsyncQueryLoop` remain free of planner/tool imports.
