# Planner Orchestration Profile Boundary Design

## Target Conclusion

Planner and intent-router agents should be production-plannable without turning
`PlannerRuntime` or `QueryLoop` into a long-running workflow engine. The SDK
should expose a deployment-facing profile that names SDK-owned planner
primitives and the deployment-owned orchestration components required for
production plan-and-execute systems: LLM decomposition policy, DAG scheduler,
worker dispatch loop, compensation policy, plan store, worker supervision, and
operational governance.

## Current State

The SDK already owns useful narrow primitives:

- `PlannerRuntime`
- `PlannerTools`
- `PlanDecomposition`
- `PlanStepSpec`
- dependency-aware `ready_steps`
- bounded `dispatch_ready_steps`
- `PlanRetryPolicy`
- `plan_fail_step`, `plan_retryable_steps`, and `plan_retry_step`
- `PlanStore`, `InMemoryPlanStore`, and `PostgresPlanStore`
- `plan_to_working_state_summary`

The production gap is not another tool handler. The gap is an explicit
deployment contract saying which orchestration components must exist outside the
SDK core before a planner/intent-router agent can be described as production
ready.

## Proposed Boundary

Add `PlannerOrchestrationDeploymentProfile` in `agentos.multi.planner`.

The profile:

- accepts configured deployment component names
- reports missing required orchestration components
- exposes `readiness_metadata()`
- exposes ASGI-compatible `readiness_check()`
- returns JSON-safe tuples/booleans/strings
- documents SDK-owned and deployment-owned responsibilities

Required components:

- `decomposition_policy`
- `dag_scheduler`
- `worker_dispatch_loop`
- `compensation_policy`
- `plan_store`
- `worker_supervision`

SDK-owned responsibilities:

- `PlannerRuntime`
- `PlannerTools`
- `PlanDecomposition ingestion`
- `dependency-ready step query`
- `bounded ready-step dispatch`
- `step failure and retry metadata`
- `PlanStore protocol`
- `working-state summary projection`

Deployment-owned responsibilities:

- `automatic LLM decomposition policy`
- `production DAG scheduler`
- `worker dispatch loop`
- `compensation orchestration`
- `worker process lifecycle`
- `live backend verification`
- `migration execution`
- `credentials and secret distribution`
- `OS/container sandboxing`

## Non-Goals

- No automatic LLM planner.
- No scheduler loop.
- No worker daemon for planner dispatch.
- No compensation workflow engine.
- No changes to `QueryLoop` or `AsyncQueryLoop`.
- No new persistent schema.

## Validation

- Planner runtime tests prove missing/ready metadata.
- Public API tests prove exports from `agentos.multi` and top-level `agentos`.
- Readiness/docs tests prove the planner form names the profile and still keeps
  automatic decomposition and scheduler policy deployment-owned.
- Runtime boundary scan proves planner concepts do not leak into query loops.
