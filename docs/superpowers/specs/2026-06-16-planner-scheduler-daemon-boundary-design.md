# Planner Scheduler Daemon Boundary Design

## Target Conclusion

Planner and intent-router agents need a production-shaped scheduler polling loop, but AgentOS should not become a workflow engine. The SDK should provide a small `PlannerSchedulerDaemon` that repeatedly invokes the existing `PlannerRuntime.scheduler_tick(...)` primitive for explicitly supplied plan ids, records JSON-safe lifecycle state, and exposes `run_once`, `start`, `stop`, `join`, `is_running`, and `state`.

## Scope

This phase adds an SDK-hosted polling shell for deployments that already know which plans should be scheduled. The daemon owns local loop lifecycle and state snapshots only.

SDK-owned:

- `PlannerSchedulerDaemonStatus`
- `PlannerSchedulerDaemonError`
- `PlannerSchedulerDaemonState`
- `PlannerSchedulerDaemon`
- Per-plan invocation of `PlannerRuntime.scheduler_tick(...)`
- Per-plan error capture without aborting the whole daemon iteration
- Immutable state snapshots for status, configured plan ids, poll interval, iterations, reports, errors, and timestamps

Deployment-owned:

- Plan discovery and tenant filtering
- Distributed locks and leader election
- Process supervision and restart policy
- Worker lifecycle execution
- Credential distribution and migrations
- Compensation orchestration
- Live backend verification

## API

`PlannerSchedulerDaemon` accepts:

- `runtime: PlannerRuntime`
- `plan_ids: tuple[str, ...]`
- `default_template_id: str | None = None`
- `retry_limit: int | None = None`
- `dispatch_limit: int | None = None`
- `poll_interval_seconds: float = 1.0`
- `clock: object | None = None`

`run_once()` returns `tuple[PlanSchedulerTickReport, ...]`. It attempts every configured plan id and records `PlannerSchedulerDaemonError` values for failures.

`start()`, `stop()`, `join(timeout)`, `is_running()`, and `state()` mirror `TeamWorkerDaemon` lifecycle semantics.

## Non-Goals

- No plan discovery API
- No distributed scheduler locks
- No background compensation engine
- No process supervisor or job runner
- No changes to `QueryLoop` or `AsyncQueryLoop`
- No hidden dependency on web runtime session state

## Testing

- Unit tests cover `run_once` report/error capture.
- Lifecycle tests cover `start`, `stop`, `join`, and running state.
- Configuration tests cover empty plan ids, empty plan id values, negative poll interval, and invalid limits.
- Architecture tests assert runtime query loops do not import the planner daemon.

