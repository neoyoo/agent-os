# Planner Claimed Scheduler Daemon Boundary Design

## Target Conclusion

Future multi-agent planner deployments need scheduler workers that can discover
schedulable plans, claim them, tick only the claimed work, and publish an
auditable local lifecycle snapshot. AgentOS should provide this narrow
claim-before-tick daemon boundary, while keeping tenant policy, global fairness,
distributed locks, leader election, process supervision, compensation, and live
backend verification deployment-owned.

## Scope

This phase adds an SDK-hosted polling shell for deployments that already use the
existing `PlannerRuntime.claimed_scheduler_tick(...)` primitive. It complements
the existing `PlannerSchedulerDaemon`, which remains the static explicit-plan-id
daemon.

SDK-owned:

- `PlannerClaimedSchedulerDaemonStatus`
- `PlannerClaimedSchedulerDaemonError`
- `PlannerClaimedSchedulerDaemonState`
- `PlannerClaimedSchedulerDaemon`
- Per-iteration invocation of `PlannerRuntime.claimed_scheduler_tick(...)`
- Local lifecycle controls: `run_once`, `start`, `stop`, `join`,
  `is_running`, and `state`
- Immutable state snapshots with worker id, owner/status filters, limits,
  lease configuration, reports, errors, iterations, and timestamps
- Error capture without losing the previous state shape

Deployment-owned:

- Tenant routing and authorization policy
- Global fairness and prioritization
- Distributed scheduler locks and leader election
- Stale claim sweep scheduling policy
- Process supervisor or job runner
- Worker dispatch loop execution beyond the existing tick primitive
- Compensation orchestration
- Credentials, migrations, alerting, and live backend verification

## API

`PlannerClaimedSchedulerDaemon` accepts:

- `runtime: PlannerRuntime`
- `worker_id: str`
- `lease_seconds: float`
- `owner_agent_id: str | None = None`
- `statuses: tuple[PlanStatus, ...] = ("draft", "running")`
- `limit: int | None = None`
- `default_template_id: str | None = None`
- `retry_limit: int | None = None`
- `dispatch_limit: int | None = None`
- `release_after_tick: bool = False`
- `poll_interval_seconds: float = 1.0`
- `clock: object | None = None`

`run_once()` returns a `PlanClaimedSchedulerTickReport`. It delegates all plan
selection, claim, tick, and optional release behavior to
`PlannerRuntime.claimed_scheduler_tick(...)`. If the runtime raises, the daemon
records a `PlannerClaimedSchedulerDaemonError` and re-raises so callers can
decide whether their process should restart.

`start()`, `stop()`, `join(timeout)`, `is_running()`, and `state()` mirror the
existing daemon lifecycle semantics.

## Non-Goals

- No new plan discovery SQL or global scheduler query planner
- No distributed scheduler lock implementation
- No leader election
- No background stale-claim sweeper
- No process supervisor or job runner
- No compensation engine
- No changes to `QueryLoop` or `AsyncQueryLoop`
- No hidden dependency on web runtime session state

## Testing

- Unit tests cover one `run_once` call passing all configuration through to
  `PlannerRuntime.claimed_scheduler_tick(...)`.
- Lifecycle tests cover `start`, `stop`, `join`, repeated polling, and immutable
  state snapshots.
- Error tests cover runtime failures and recorded daemon errors.
- Configuration tests cover empty worker id, invalid lease, invalid limits,
  invalid statuses, and invalid poll interval.
- Architecture tests assert runtime query loops do not import the claimed
  scheduler daemon.
