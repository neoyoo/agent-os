# Planner Plan Claim Lease Boundary Design

> Branch: `review/agentos-sdk-architecture-20260611`

## Target Conclusion

Multi-node planner schedulers need an SDK-level claim/lease boundary so two
workers do not tick the same schedulable plan at the same time. AgentOS should
provide JSON-safe claim records, a small `PlanClaimStore` protocol, an
in-memory implementation, and a `PlannerRuntime` helper that composes
schedulable-plan selection with claim acquisition.

This is not a distributed scheduler. Leader election, tenant authorization,
global fairness, live Redis/Postgres locking, migrations, process supervision,
worker execution, and compensation orchestration remain deployment-owned.

## Current Gap

Phase 73 added deterministic `PlannerRuntime.schedulable_plans(...)` summaries
and the `plan_schedulable_plans` tool. Those summaries tell deployment-owned
schedulers which plans have ready or due-retry work, but they do not prevent
multiple scheduler workers from selecting and ticking the same plan
concurrently.

## SDK-Owned Boundary

- `PlanClaimRecord` captures `plan_id`, `owner_agent_id`, `worker_id`,
  `claimed_at`, `lease_expires_at`, and a monotonic `generation`.
- `PlanClaimResult` returns either `claimed` with the new claim or `busy` with
  the existing unexpired claim.
- `PlanClaimStore` defines `claim_plan`, `release_plan`, and `get_claim`.
- `InMemoryPlanClaimStore` supports local tests and single-process schedulers.
- `PlannerRuntime.claim_schedulable_plans(...)` filters plans through
  `schedulable_plans(...)` and then attempts claims through the injected claim
  store.
- `PlannerTools` exposes `plan_claim_schedulable_plans` as an owner-scoped
  JSON tool.

## Deployment-Owned Boundary

- Redis/Postgres compare-and-set, row locks, advisory locks, or stream-group
  claiming semantics.
- Tenant authorization and cross-tenant filtering.
- Global fairness, priority queues, and starvation prevention.
- Leader election and scheduler worker membership.
- Scheduler process supervision, graceful shutdown, and live backend probes.
- Claim sweeper jobs, alerting, and migration rollout.

## Acceptance Criteria

- In-memory claims can be acquired, renewed by the same worker, rejected as busy
  for another worker, released by the owning worker, and taken over after lease
  expiry.
- Claim records and results are JSON-safe through `as_dict()`.
- `PlannerRuntime.claim_schedulable_plans(...)` requires a claim store and
  remains owner/status/limit scoped.
- `PlannerTools` exposes an owner-scoped `plan_claim_schedulable_plans` tool.
- Public exports include the claim store protocol, in-memory adapter, claim
  record, result, and status type.
- Readiness, production docs, objective audit, roadmap, and the agent-os skill
  describe the new SDK primitive without claiming production distributed locks.
- `QueryLoop` and `AsyncQueryLoop` remain free of planner, claim, lock, team,
  and A2A orchestration concepts.
