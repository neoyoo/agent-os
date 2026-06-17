# Planner Scheduler Tick Boundary Design

## Target Conclusion

Planner and intent-router agents need an SDK-owned scheduling primitive, but not
a long-running workflow engine inside `QueryLoop`, `AsyncQueryLoop`, or
`PlannerRuntime`. The SDK should expose a one-shot scheduler tick that an
external daemon, cron, queue worker, or app loop can call to reset due retries
and dispatch dependency-ready steps through the existing coordinator boundary.
Worker processes, long-running polling, queue leases, credentials, and complex
compensation remain deployment-owned.

## Current State

AgentOS already has:

- `PlannerRuntime` with `ready_steps()`, `retryable_steps()`,
  `retry_step()`, and `dispatch_ready_steps()`.
- `PlannerTools` exposing those operations as external tools.
- `PlanDispatchReport` and `PlanDispatchSkip` for bounded ready-step dispatch.
- `PlanRetryPolicy` and failed-step metadata for auditable retry state.
- `PlannerOrchestrationDeploymentProfile` to name production orchestration
  components.

The missing SDK boundary is the production-shaped unit of scheduling work:
"perform one scheduler pass for this plan." Today deployments must manually
chain retry reset plus ready-step dispatch and invent their own summary format.

## Proposed Boundary

Add two small dataclasses:

- `PlanSchedulerRetryReset`: records one due failed step moved back to
  `pending`, preserving attempt count.
- `PlanSchedulerTickReport`: records the plan id, retry resets, and the
  existing `PlanDispatchReport` for assigned/skipped steps.

Add `PlannerRuntime.scheduler_tick(...)`:

- Validates `retry_limit` and `dispatch_limit` when provided.
- Lists due retryable steps.
- Resets due retryable steps back to pending through `retry_step()`, bounded by
  `retry_limit`.
- Calls `dispatch_ready_steps(...)`, bounded by `dispatch_limit`.
- Returns a structured report.

Add `PlannerTools` operation `plan_scheduler_tick` so a main agent or
deployment-owned scheduler agent can call the same primitive through the tool
boundary.

## Non-Goals

- No background thread, async loop, sleep loop, or daemon implementation.
- No worker process lifecycle manager.
- No queue lease or distributed lock implementation.
- No automatic LLM decomposition policy.
- No compensation engine beyond existing failure/retry metadata.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Readiness And Guidance

Update readiness/docs/skill language from "SDK has ready-step dispatch but
production DAG scheduler remains app-owned" to "SDK has a one-shot scheduler
tick; the production scheduler daemon/loop, worker lifecycle, and compensation
policy remain deployment-owned."

The objective coverage audit should move the planner scheduler item from a
missing boundary to a primitives-ready boundary while still keeping the
long-running goal active.
