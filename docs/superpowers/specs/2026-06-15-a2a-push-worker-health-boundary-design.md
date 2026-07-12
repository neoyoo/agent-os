# A2A Push Worker Health Boundary Design

## Target Conclusion

A2A push notification delivery should be observable by a deployment supervisor,
not merely runnable as a background loop. The SDK should expose a narrow health
projection over `A2APushNotificationDaemonState` so deployments can classify a
worker as healthy, degraded, unhealthy, stopped, stale, or unstarted without
coupling the runtime loop to webhook delivery. Process supervision, alerting
systems, service discovery, and orchestration remain deployment responsibilities.

## Problem

The SDK already has:

- `A2APushNotificationDeliveryWorker`
- `A2APushNotificationDaemon`
- `A2APushNotificationDaemonState`
- persistent push notification config and delivery stores
- retry/dead-letter primitives

However, production operators still need a stable way to decide whether the
worker is healthy enough to keep receiving traffic or whether a supervisor
should alert/restart it. Today consumers can inspect raw daemon state, but every
deployment would need to reinterpret status, last run time, and accumulated
errors on its own.

## Scope

In scope:

- Add `A2APushNotificationHealthStatus`.
- Add `A2APushNotificationHealthReport`.
- Add `A2APushNotificationHealthPolicy`.
- Add a helper on `A2APushNotificationDaemon` to return a health report.
- Export the new API through `agentos.channels` and top-level `agentos`.
- Update readiness, production docs, skill docs, and roadmap.

Out of scope:

- No ASGI health endpoint in this phase.
- No Prometheus/OpenTelemetry exporter.
- No process supervisor.
- No daemon restart policy.
- No changes to delivery retry semantics.
- No runtime loop imports.

## Health Semantics

The policy should use only the immutable daemon state and a supplied `now`
timestamp:

- `unstarted`: no iterations and no `last_run_at`.
- `stopped`: daemon status is `stopped`, unless a stronger unhealthy reason is
  present.
- `unhealthy`: recent polling errors are at or above the configured unhealthy
  threshold, or the last run is beyond `max_stale_seconds`.
- `degraded`: recent polling errors are at or above the degraded threshold.
- `healthy`: no stale condition and recent errors are below degraded threshold.

Recent errors are counted by `raised_at >= now - error_window_seconds`.

The report should include:

- status
- reason
- worker id
- daemon status
- iterations
- last run timestamp
- seconds since last run
- recent error count
- last error text

## Verification

Tests should prove:

- fresh unstarted state reports `unstarted`;
- a recent successful run reports `healthy`;
- a stale last run reports `unhealthy`;
- one recent error reports `degraded` when threshold is one;
- enough recent errors report `unhealthy`;
- stopped state reports `stopped`;
- `A2APushNotificationDaemon.health()` delegates to the policy;
- runtime loops do not import or mention push health classes.
