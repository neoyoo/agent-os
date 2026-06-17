# Planner Scheduler Governance Profile Boundary Design

## Target Conclusion

Future production planner clusters need more than a local claimed scheduler
daemon. They need an explicit readiness contract for plan discovery policy,
tenant routing, global fairness, distributed scheduler locks, leader election,
stale lease recovery, worker dispatch supervision, and live backend
verification. AgentOS should expose this as a narrow governance profile so
apps can prove which production controls are configured, while the actual
global scheduler, lock service, election mechanism, routing policy, and live
backend verification remain deployment-owned.

## Scope

This phase adds a deployment-facing profile that complements:

- `PlannerRuntime.schedulable_plans(...)`
- `PlannerRuntime.claim_schedulable_plans(...)`
- `PlannerRuntime.claimed_scheduler_tick(...)`
- `PlannerClaimedSchedulerDaemon`
- `PlannerWorkerDispatchSupervisionProfile`
- `PlannerStaleClaimSweepProfile`

SDK-owned:

- `PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS`
- `PlannerSchedulerGovernanceDeploymentProfile`
- JSON-safe readiness metadata with required/configured/missing components
- ASGI-compatible `readiness_check()` payload
- Public exports through `agentos.multi` and top-level `agentos`
- Readiness, production docs, objective audit, roadmap, and skill guidance

Deployment-owned:

- Tenant routing and authorization policy
- Global fairness and prioritization policy
- Distributed scheduler lock implementation
- Leader election mechanism
- Stale lease recovery policy and schedule
- Real worker dispatch execution and compensation orchestration
- Credentials, migrations, alerting, and live backend verification

## API

`PlannerSchedulerGovernanceDeploymentProfile` accepts:

- `configured_components: tuple[str, ...] = ()`
- `required_components: tuple[str, ...] = PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS`
- `probe_name: str = "planner_scheduler_governance"`

Default required components:

- `plan_discovery_policy`
- `tenant_routing_policy`
- `global_fairness_policy`
- `scheduler_lock_policy`
- `leader_election_policy`
- `stale_lease_recovery_policy`
- `worker_dispatch_supervision`
- `live_backend_verification`

`missing_components()` returns required components not present in
`configured_components`.

`readiness_metadata()` returns:

- profile name
- probe name
- ready boolean
- required/configured/missing components
- SDK-owned primitives
- deployment-owned controls

`readiness_check()` returns the same metadata with `ok` and `status`.

## Non-Goals

- No distributed scheduler lock implementation
- No leader election implementation
- No global fairness queue
- No tenant directory integration
- No worker process supervisor
- No compensation engine
- No live backend probe implementation
- No changes to `QueryLoop` or `AsyncQueryLoop`

## Testing

- Unit tests cover default missing components and configured ready state.
- Validation tests reject empty probe names and blank component names.
- Public API tests require the profile in `agentos.multi` and top-level
  `agentos`.
- Readiness tests require planner intent-router evidence to name the profile.
- Docs tests require production readiness, objective audit, roadmap, and skill
  guidance to name the profile while preserving deployment-owned boundaries.
- Runtime architecture scan confirms query loops remain free of planner/A2A/team
  governance concepts.
