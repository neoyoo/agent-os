# Planner Claimed Scheduler Tick Boundary Design

## Target Conclusion

AgentOS should provide a small SDK-owned composition boundary for distributed
planner workers: select schedulable plans, claim each plan through the injected
`PlanClaimStore`, and run a one-shot scheduler tick only for plans successfully
claimed by the current worker. This makes multi-node planner workers safer to
wire without turning AgentOS into a full scheduler platform.

Deployment still owns plan discovery sources, leader election, global fairness,
distributed lock tuning, stale-lease sweepers, tenant authorization, credentials,
migration execution, process supervision, autoscaling, compensation
orchestration, and live backend verification.

## Current Gap

Phase 73 added schedulable plan selection. Phase 74 added local claim/lease
primitives. Phase 75 added `PostgresPlanClaimStore`. Phase 69 added a one-shot
`scheduler_tick(...)` primitive. A deployment can manually compose these, but
there is no SDK-level helper that proves busy claims are skipped before
dispatch, or that a worker can release its own lease after a bounded tick.

## SDK Boundary

- Add `PlannerRuntime.claimed_scheduler_tick(...)`.
- Add a JSON-safe report dataclass for the combined operation.
- Add a JSON-safe skip dataclass for plans that were busy or failed during the
  tick.
- Add `plan_claimed_scheduler_tick` to `PlannerTools`.
- Keep owner scoping in `PlannerTools`; the tool selects only plans owned by the
  tool owner and never accepts arbitrary `plan_id` input.
- Support `release_after_tick` so deployment-owned daemons can use short claims
  for cooperative worker batches.
- Keep `QueryLoop` and `AsyncQueryLoop` unaware of planner, team, A2A, or claim
  concepts.

## Non-Goals

- No scheduler leader election.
- No global fairness algorithm.
- No stale-lease sweeper.
- No worker supervisor or job runner.
- No automatic LLM decomposition policy.
- No tenant authorization implementation.
- No database migration runner or credential loader.

## Verification

- Runtime tests prove only claimed plans are ticked, busy plans are skipped, and
  optional release lets another worker claim later.
- Tool tests prove owner scoping, registration, JSON shape, validation, and
  `release_after_tick`.
- Public API tests lock the new report dataclasses.
- Docs/readiness/audit tests lock the production boundary language.
