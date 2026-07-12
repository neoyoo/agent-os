# Planner Worker Dispatch Supervision Profile Design

## Target Conclusion

Distributed planner workers need a stable supervision projection for
claim-before-tick dispatch batches, but AgentOS should not become a process
supervisor or job runner. The SDK should expose a JSON-safe
`PlannerWorkerDispatchSupervisionProfile` that consumes
`PlanClaimedSchedulerTickReport` history, summarizes recent dispatch health,
and reports deployment-owned lifecycle, lock, stale-lease, compensation,
credential, migration, and live-backend gaps.

## Scope

This phase adds a deployment-facing profile around existing planner scheduler
reports. It does not start worker processes, discover plans, run leader
election, sweep stale leases, execute compensation, manage credentials, or
verify live backends.

## SDK-Owned Boundary

- Accept recent `PlanClaimedSchedulerTickReport` values from
  `PlannerRuntime.claimed_scheduler_tick(...)` or
  `plan_claimed_scheduler_tick`.
- Produce JSON-safe readiness metadata and health/readiness checks.
- Count tick reports, claims, released claims, busy skips, and tick-failed
  skips.
- Report consecutive failed dispatch batches so process supervisors can alert or
  restart without parsing planner internals.
- Validate configured deployment component names.

## Deployment-Owned Boundary

- Real process supervisor or job runner.
- Worker lifecycle execution, restart, graceful drain, and autoscaling.
- Plan discovery sources, tenant filtering, distributed scheduler locks, leader
  election, stale-lease sweepers, and fairness policy.
- Compensation orchestration for partially dispatched plans.
- Credentials, migration execution, alert routing, runbooks, and live backend
  verification.
- OS/container sandboxing for generated subagent work.

## API Shape

The profile lives in `agentos.multi.planner` and is exported from
`agentos.multi` and top-level `agentos`.

```python
profile = PlannerWorkerDispatchSupervisionProfile(
    reports=(report_a, report_b),
    configured_components=(
        "claimed_scheduler_tick_loop",
        "worker_process_lifecycle",
        "plan_claim_store",
        "scheduler_lock_policy",
        "stale_lease_recovery",
        "compensation_policy",
        "metrics_alerting",
        "live_backend_verification",
    ),
)

profile.health_payload()
profile.readiness_metadata()
profile.readiness_check()
```

Readiness is true only when required deployment components are configured and
the report history does not exceed the consecutive-failure threshold.

## Testing

- Unit tests for missing components and ready metadata.
- Unit tests for healthy, degraded, and failed report histories.
- Public API export tests.
- Readiness/docs tests that keep this phase discoverable.

