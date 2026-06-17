# Postgres Plan Claim Store Boundary Design

## Target Conclusion

AgentOS should provide a durable Postgres-backed `PlanClaimStore` adapter so
multi-node scheduler workers can coordinate plan leases through a real shared
state boundary. The SDK owns the adapter contract, JSON-safe records, atomic
claim/renew/release semantics, and migration shape. Deployment still owns
leader election, global fairness, lock tuning, stale-lease policy, credentials,
migration execution, process supervision, tenant authorization, and live
backend verification.

## Current Gap

Phase 74 added `PlanClaimStore`, `PlanClaimRecord`,
`InMemoryPlanClaimStore`, `PlannerRuntime.claim_schedulable_plans(...)`, and
the `plan_claim_schedulable_plans` tool. That covers local schedulers and unit
tests, but production web or planner workers running on different nodes need a
shared lease store. The current readiness matrix still lists production
distributed claim stores and distributed scheduler locks as deployment-owned
blockers.

## SDK Boundary

- Add `PostgresPlanClaimStore` in `src/agentos/multi/postgres_plan.py`.
- Keep `PlanStore` and `PlanClaimStore` separate: plan state remains durable
  truth, while claim rows are transient scheduler leases.
- Use parameterized SQL only.
- Claim succeeds when no row exists, the row is expired, or the same worker
  renews it.
- Claim returns `busy` with the existing `PlanClaimRecord` when another worker
  owns an unexpired lease.
- Release only removes a row for the matching worker.
- `get_claim(...)` returns the persisted record without deciding whether it is
  expired.

## Non-Goals

- No Redis adapter in this phase.
- No scheduler leader election or global fairness algorithm.
- No automatic stale-lease sweeper.
- No app tenant authorization or RBAC integration.
- No migration runner, credential loader, or live backend health probe.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Verification

- Add fake-connection unit tests for the adapter behavior.
- Add migration tests for `agentos_plan_claims` table and indexes.
- Add public API export tests.
- Update readiness/docs/skill/audit/roadmap to move the durable Postgres claim
  adapter into SDK evidence while keeping distributed scheduler locks and
  operational controls deployment-owned.
