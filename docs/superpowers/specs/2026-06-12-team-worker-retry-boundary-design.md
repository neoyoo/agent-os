# Team Worker Retry Boundary Design (Phase 16A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 15A team worker daemon

## Target Conclusion

```text
TeamWorkerDaemon solves continuous hosting; production team workers also need a
retry/backoff boundary so failed worker continuations do not hot-loop. The SDK
should provide a replaceable retry store and policy, while keeping cancellation,
dead-letter transport integration, and permission enforcement as later phases.
```

## Problem

Phase 14A leaves failed team-message deliveries unacked. Phase 15A can run the
worker loop continuously. Together, those primitives are enough to host workers,
but they still do not define what happens when the same delivery keeps failing.

Different queue adapters behave differently:

- `AgentInbox.collect()` drains local deliveries, so local retry needs runner
  state if a delivery should be attempted again.
- `RedisAgentMessageQueue` keeps failed messages pending until a reclaim policy
  reclaims or dead-letters them.

The SDK needs a runner-level retry boundary that is independent of the queue
transport. It should prevent hot loops and make retry state observable without
forcing `AgentMessageQueue` to become a scheduler.

## Scope

Phase 16A adds:

- `TeamWorkerRetryPolicy`
- `TeamWorkerRetryStatus`
- `TeamWorkerRetryRecord`
- `TeamWorkerRetryStore`
- `InMemoryTeamWorkerRetryStore`
- optional `retry_policy` / `retry_store` on `TeamWorkerRunner`
- retry status projection in `TeamWorkerRunResult`
- retry snapshots in `TeamWorkerDaemonState`

Out of scope:

- cross-node persistent retry adapter
- transport-level dead-letter integration
- team cancellation policy
- permission downgrade enforcement
- UI stream protocol

## Semantics

`TeamWorkerRetryPolicy(max_attempts=3, backoff_seconds=1.0)` means:

- first worker failure is attempt `1`
- if `attempt < max_attempts`, the delivery is stored as `scheduled` with
  `next_run_at = now + delay_for_attempt(attempt)`
- while `now < next_run_at`, the runner reports `retry_skipped` and does not
  call `run_continuation()`
- once due, the runner retries the stored `QueueDelivery`
- success clears the retry record and acks the delivery
- when `attempt >= max_attempts`, the retry record becomes `exhausted`

`ack_exhausted` defaults to `False` because some transports should dead-letter
through adapter-level policy rather than silently ack. Hosts that want the SDK
runner to stop transport redelivery can set `ack_exhausted=True`.

## Store Boundary

`TeamWorkerRetryStore` is a protocol so production deployments can replace the
default in-memory store with a durable/distributed adapter.

The first implementation is `InMemoryTeamWorkerRetryStore`. It is thread-safe
and suitable for local service workers, deterministic tests, and single-node
deployment.

## Acceptance Criteria

- A failing worker delivery with retry policy records a scheduled retry instead
  of being only a raw failure.
- The runner does not call the worker again before `next_run_at`.
- A due retry runs from retry store even if the local queue has already drained
  the original delivery.
- Success clears the retry record and acks the delivery.
- Exhausted attempts are observable and can optionally ack the delivery.
- `TeamWorkerDaemonState` exposes current retry records.
- Public API exports include retry policy/store/status/record symbols.
- Readiness/docs describe retry/backoff as SDK primitives, while persistent
  retry store, cancellation, permission, and UI stream remain gaps.
- Runtime loop files do not mention retry/team worker types.
