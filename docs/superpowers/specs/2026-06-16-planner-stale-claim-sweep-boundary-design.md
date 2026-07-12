# Planner Stale Claim Sweep Boundary Design

## Target Conclusion

Distributed planner workers need a standard recovery boundary for expired plan
claims, but AgentOS should not own cron scheduling, leader election, or global
fairness policy. The SDK should expose a race-safe, JSON-safe stale-claim sweep
operation and readiness profile so deployments can detect and release expired
planner leases with auditable evidence.

## Scope

This phase adds an SDK boundary for finding and optionally releasing expired
planner claim records. It does not run a background sweeper, choose a global
leader, decide tenant fairness, perform compensation, issue credentials, or
verify live production backends.

## SDK-Owned Boundary

- Scan expired `PlanClaimRecord` values through a claim store that implements
  stale sweep operations.
- Release only the exact expired claim that was inspected by matching plan id,
  owner, worker, generation, and expiry.
- Return a `PlanClaimSweepReport` that is safe to serialize and attach to
  readiness, audit logs, and runbooks.
- Provide a dry-run mode that reports expired claims without mutating state.
- Provide a `PlannerStaleClaimSweepProfile` that summarizes recent sweep
  reports and deployment component readiness.

## Deployment-Owned Boundary

- Cron, queue, or scheduler that invokes the sweep.
- Distributed scheduler locks, leader election, tenant filters, and fairness.
- Alert routing, incident runbooks, live backend checks, and rollout policy.
- Compensation orchestration for plans whose workers died mid-dispatch.
- Credentials, schema migration execution, and OS/container sandboxing.

## API Shape

```python
report = runtime.sweep_expired_claims(
    owner_agent_id="leader",
    now=120.0,
    limit=50,
    dry_run=False,
)

report.as_dict()

profile = PlannerStaleClaimSweepProfile(
    reports=(report,),
    configured_components=(
        "stale_claim_sweep_schedule",
        "plan_claim_store",
        "scheduler_lock_policy",
        "sweep_safety_window",
        "metrics_alerting",
        "live_backend_verification",
    ),
)

profile.readiness_check()
```

The operation is intentionally runtime/admin oriented and is not registered as
an LLM-facing planner tool.

## Testing

- Unit tests for dry-run and releasing expired in-memory claims.
- Unit tests proving stale release is generation-safe.
- Postgres adapter tests for parameterized expired-claim query and guarded
  delete.
- Profile and public API export tests.
- Readiness/docs tests to keep the new boundary visible in the long-running
  objective audit.
