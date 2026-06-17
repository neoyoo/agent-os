# Team Worker Cancellation Boundary Design (Phase 17A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 15A daemon and Phase 16A/16B retry stores

## Target Conclusion

```text
Team workers need a cancellation intent boundary that the runner can honor
before starting a continuation turn. The SDK should support delivery-level and
worker-level cancellation scheduling without coupling QueryLoop to team
semantics, and without mixing permission downgrade or UI streaming into this
phase.
```

## Problem

Team workers now have independent sessions, a runner, a daemon, and persistent
retry/backoff state. The remaining production gap is that callers cannot ask the
team worker scheduler to stop a queued or retry-delayed continuation before it
starts.

Stopping the whole daemon or deleting a team is too coarse. Production team
workers need a targeted cancel intent:

- cancel one pending delivery/message before `run_continuation()`
- cancel all pending worker continuations for a worker session
- expose cancellation records in daemon state
- keep runtime loops deployment-agnostic

## Scope

Phase 17A adds:

- `TeamWorkerCancellationStatus`
- `TeamWorkerCancellationRecord`
- `TeamWorkerCancellationStore`
- `InMemoryTeamWorkerCancellationStore`
- optional `cancellation_store` on `TeamWorkerRunner`
- `cancelled` results in `TeamWorkerRunResult`
- cancellation records in `TeamWorkerDaemonState`

Out of scope:

- Postgres cancellation store
- interrupting an already-running `run_continuation()` turn
- permission downgrade enforcement
- UI stream protocol

## Semantics

Cancellation records can be exact or worker-scoped:

- exact delivery cancel: `delivery_id` or `message_id` is set
- worker-scope cancel: both `delivery_id` and `message_id` are unset

Runner behavior:

- before running a queued delivery, check active cancellation records
- before retrying a stored retry record, check active cancellation records
- if cancelled, do not call `run_continuation()`
- ack the delivery when a delivery object is available
- clear retry state for cancelled retry records
- exact cancellation records are acknowledged after the delivery is skipped
- worker-scope cancellation records stay requested until app/profile code clears
  them, so future deliveries are also skipped

## Acceptance Criteria

- A delivery-level cancellation skips a queued team-message delivery, acks it,
  does not call the worker agent, and acknowledges the cancellation record.
- A worker-level cancellation skips matching deliveries and stays requested.
- A cancellation can skip a retry-store delivery even before retry backoff is
  due, and clears retry state.
- `TeamWorkerDaemonState` exposes current cancellation records.
- Public API exports cancellation types.
- Readiness/docs describe cancellation scheduling as SDK primitives while
  keeping permission downgrade and UI stream protocol as gaps.
- Runtime loop files do not mention team worker cancellation types.
