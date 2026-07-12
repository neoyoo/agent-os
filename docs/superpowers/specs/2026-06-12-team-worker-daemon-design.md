# Team Worker Daemon Design (Phase 15A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 14A team worker runner

## Target Conclusion

```text
Team discussion needs a service-hostable worker loop. The SDK should provide a
daemon boundary that repeatedly invokes TeamWorkerRunner and exposes lifecycle
state, while keeping retry/backoff, cancellation, permission enforcement, and UI
streaming as separate production policy phases.
```

## Problem

Phase 14A added `TeamWorkerRunner`, which can process one current batch of
team-message deliveries and turn each delivery into a worker
`run_continuation()` call. That is enough for tests and manually scheduled
workers, but it is not enough for a production service shape where worker
processes are long-running hosts.

Without an SDK-owned daemon boundary, every application has to invent:

- how to start and stop the worker polling loop
- how to wait for shutdown during service drain
- where to observe the most recent runner results
- whether team semantics leak into `QueryLoop`

The SDK should own the generic host loop. Applications should still own the
deployment policy around retry, cancellation, permissions, and UI streams.

## Scope

Phase 15A adds a narrow daemon wrapper around `TeamWorkerRunner`.

In scope:

- `TeamWorkerDaemon`
- `TeamWorkerDaemonState`
- `TeamWorkerDaemonStatus`
- `start()`, `stop()`, `join()`, `is_running()`, `run_once()`, `state()`
- repeated polling of `TeamWorkerRunner.run_pending(team_id=...)`
- state snapshots containing iteration count, timestamps, last results, and
  runner errors
- public API exports and readiness/docs updates

Out of scope:

- retry or backoff policy
- cancellation policy
- dead-letter handling
- permission downgrade enforcement
- UI stream/event protocol
- changing `QueryLoop` or `AsyncQueryLoop`

## API Shape

```python
daemon = TeamWorkerDaemon(
    runner=runner,
    team_id="team_1",
    poll_interval_seconds=0.5,
)

daemon.start()
daemon.stop()
daemon.join(timeout=5.0)
snapshot = daemon.state()
```

`run_once()` remains available for cron-style hosts and deterministic tests:

```python
results = daemon.run_once()
```

`TeamWorkerDaemonState` is immutable and safe to hand to diagnostics:

```python
TeamWorkerDaemonState(
    status="running",
    team_id="team_1",
    poll_interval_seconds=0.5,
    iterations=12,
    started_at=1781250000.0,
    stopped_at=None,
    last_run_at=1781250004.5,
    last_results=(...),
    errors=(...),
)
```

## Lifecycle Semantics

- `start()` starts one background daemon thread if not already running.
- Repeated `start()` while running is idempotent.
- `stop()` requests shutdown and moves state to `stopping` while the thread is
  still alive.
- `join(timeout=...)` waits for the background thread and returns `True` when
  stopped.
- `run_once()` can be called without `start()` and records the same state as one
  loop iteration.
- The loop uses `Event.wait(poll_interval_seconds)` so shutdown does not wait
  for a full sleep interval.

## Failure Semantics

Worker delivery failures remain `TeamWorkerRunner` results. The daemon records
the latest result batch and the runner's accumulated errors so hosts can expose
them through health or metrics.

Unexpected exceptions from `TeamWorkerRunner.run_pending()` should be recorded
as daemon-level failed state only in a later retry/backoff phase. Phase 15A keeps
the runner contract narrow and does not introduce a scheduler policy.

## Boundary Invariant

`QueryLoop` and `AsyncQueryLoop` must not import or mention
`TeamWorkerDaemon`. Runtime loops stay deployment-agnostic; profile or
application host code assembles team worker daemons.

## Acceptance Criteria

- `TeamWorkerDaemon.run_once()` calls `TeamWorkerRunner.run_pending()` with the
  configured `team_id` and records last results/errors.
- `start()` repeatedly invokes the runner until `stop()` is requested.
- `join()` confirms the daemon thread exits.
- Worker failures returned by `TeamWorkerRunner` are visible through daemon
  state.
- Public API exports include `TeamWorkerDaemon`, `TeamWorkerDaemonState`, and
  `TeamWorkerDaemonStatus`.
- Readiness/docs no longer list daemon dispatch itself as missing, but still
  list retry/backoff, cancellation scheduling, permission downgrade policy, and
  UI stream protocol as production gaps. Later Phase 16A adds retry/backoff
  primitives.
- Runtime loop files do not mention `TeamWorkerDaemon`.
