# Planner Schedulable Plan Selection Boundary Design

## Target Conclusion

Planner scheduler daemons need a narrow SDK primitive for selecting which plans are worth ticking from `PlanStore`, but AgentOS must not own tenant authorization, distributed locks, leader election, global fairness, or process supervision. The SDK should expose deterministic schedulable-plan summaries that filter plans by owner, status, ready pending steps, and due retryable failed steps, returning JSON-safe records that deployment-owned schedulers can claim, lock, or supervise outside the SDK.

## Scope

This phase adds a runtime query boundary, not a distributed scheduler. `PlannerRuntime.schedulable_plans(...)` will inspect plans returned by `PlanStore.list_plans(owner_agent_id)` and include only plans with dependency-ready pending steps or due retryable failed steps. The method will preserve store order, apply `limit` after filtering, and return immutable `PlannerSchedulablePlan` summaries.

## SDK-Owned

- Validate `statuses` and `limit` inputs.
- Filter by owner via the existing `PlanStore.list_plans(owner_agent_id)` contract.
- Filter by plan status, defaulting to `draft` and `running`.
- Detect ready work via `PlannerRuntime.ready_steps(plan_id)`.
- Detect retryable work via `PlannerRuntime.retryable_steps(plan_id)`.
- Return JSON-safe summaries with plan id, owner, status, ready step ids, retryable step ids, reasons, and `updated_at`.
- Expose an LLM-callable `plan_schedulable_plans` tool scoped to `PlannerTools.owner_agent_id`.

## Deployment-Owned

- Tenant authorization beyond `owner_agent_id` scoping.
- Plan claiming, leases, distributed locks, and leader election.
- Process supervision, autoscaling, and daemon restart policy.
- Fairness across tenants or queues.
- Worker dispatch loop supervision and compensation orchestration.
- Live backend verification, credentials, and migration execution.

## Non-Goals

- Do not change `PlanStore` or `PostgresPlanStore` contracts.
- Do not mutate plans during selection.
- Do not teach `PlannerSchedulerDaemon` to discover plans in this phase.
- Do not import planner/A2A/team concepts into `QueryLoop` or `AsyncQueryLoop`.

## Acceptance Criteria

- `PlannerRuntime.schedulable_plans(...)` returns ready and due-retry plans as immutable summaries.
- Owner, status, and limit filtering are deterministic and tested.
- Invalid status filters and invalid limits are rejected before plan lookup work.
- `PlannerTools` exposes `plan_schedulable_plans` and preserves owner scoping.
- Public API exports include `PlannerSchedulablePlan` and `PlannerSchedulablePlanReason`.
- Readiness, production guidance, objective audit, roadmap, and agent-os skill docs reflect that plan selection is SDK-owned while locks and discovery governance remain deployment-owned.
