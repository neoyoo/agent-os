# Planner Worker Dispatch Boundary Design

Date: 2026-06-15

## Target Conclusion

Planner should not become a full production DAG scheduler, but the SDK should
own a narrow worker-dispatch boundary. A planner can ask for dependency-ready
steps to be submitted to the existing coordinator task boundary, receive an
auditable dispatch report, and leave loop scheduling, worker scaling,
compensation, and decomposition policy to the application or runtime profile.

## Design

- `PlannerRuntime.dispatch_ready_steps(...)` reads the current ready-step set
  from `ready_steps(plan_id)`.
- Each ready step is submitted through the existing `assign_step(...)` path
  using the step's `template_id` or an explicit `default_template_id`.
- Successful submissions append normal `PlanAssignment` records and move steps
  to `assigned`.
- Steps without a usable template are not mutated. They are reported as skipped
  with a stable reason.
- Coordinator submission failures are captured as skipped records. Previously
  assigned steps remain assigned because each assignment is already persisted by
  `assign_step`.
- A `limit` parameter lets an app-owned scheduler dispatch a bounded batch.
- `PlanDispatchReport` is a transient report, not durable plan state.
- `PlannerTools` exposes `plan_dispatch_ready_steps` so a leader agent or
  scheduler can invoke the boundary without direct Python access.

## Non-Goals

- No automatic LLM decomposition policy.
- No long-running DAG scheduler loop.
- No worker process lifecycle manager.
- No result polling, compensation, or retry policy beyond existing planner and
  coordinator primitives.
- No QueryLoop or AsyncQueryLoop integration.

## Acceptance Criteria

- Runtime tests prove ready steps are assigned in a bounded batch.
- Runtime tests prove blocked/already-assigned steps are not submitted.
- Runtime tests prove missing templates and coordinator failures are reported
  without mutating those steps.
- Tool tests prove `plan_dispatch_ready_steps` is registered, owner-scoped, and
  returns an auditable JSON report.
- Public API exports include the dispatch report types.
- Readiness, skill docs, and roadmap distinguish the new dispatch boundary from
  a production scheduler loop.
