# A2A Push Worker Deployment Health Profile Design

## Target Conclusion

A2A push notification workers should not only expose an internal health
projection. The SDK should provide a narrow deployment-facing profile that turns
daemon health into JSON-safe health/readiness checks for ASGI hosts and service
supervisors. The SDK should not own process supervision, restart policy,
alerting, credentials, DNS pinning, enterprise egress proxying, CA rollout,
tenant RBAC, or external conformance execution.

## Problem

`A2APushNotificationDaemon.health()` already classifies daemon state as
healthy, degraded, unhealthy, stopped, or unstarted. Operators still need to
wire that report into deployment checks by hand. Without a standard profile,
each application must decide its own payload shape, readiness mapping, and
metadata format before it can expose worker status through `/ready`,
Kubernetes probes, or a load balancer.

## Scope

In scope:

- Add `A2APushNotificationDeploymentProfile`.
- Convert `A2APushNotificationHealthReport` into a JSON-safe payload.
- Expose `health_check()` for liveness-style inspection.
- Expose `readiness_check()` for `AsgiAgentApp(readiness_checks=...)`.
- Treat only `healthy` as readiness-ok by default.
- Expose readiness metadata describing worker id, probe name, accepted
  readiness statuses, and deployment-owned responsibilities.
- Export the profile through `agentos.channels` and top-level `agentos`.
- Update readiness, production docs, skill docs, roadmap, and public API tests.

Out of scope:

- Do not change the default `/v1/health` response.
- Do not add a new endpoint router.
- Do not start, stop, or restart daemon processes.
- Do not add Kubernetes manifests or Prometheus exporters.
- Do not add CA/DNS/egress/credential governance.
- Do not import A2A/channel code into runtime query loops.

## API Shape

```python
profile = A2APushNotificationDeploymentProfile(
    daemon=daemon,
    policy=A2APushNotificationHealthPolicy(max_stale_seconds=30.0),
    probe_name="a2a_push_worker",
)

app = AsgiAgentApp(
    sessions=sessions,
    readiness_checks={profile.probe_name: profile.readiness_check},
)
```

`health_payload()` returns:

- `status`: SDK health status.
- `ok`: boolean readiness/liveness interpretation.
- `reason`.
- `worker_id`.
- `daemon_status`.
- `iterations`.
- `last_run_at`.
- `seconds_since_last_run`.
- `recent_error_count`.
- `last_error`.

`readiness_check()` returns the same payload with `status` normalized to `ok`
or `failed`, preserving the SDK health status under `health_status`, because
`AsgiAgentApp._handle_ready()` already treats `status in {"ok", "ready", True}`
as passing.

## Readiness Mapping

Default readiness behavior is intentionally conservative:

- `healthy` -> ready.
- `degraded`, `unhealthy`, `stopped`, and `unstarted` -> not ready.

Deployments can override `ready_statuses` if a supervisor should keep a
degraded worker in rotation while alerting out-of-band.

## Verification

Tests should prove:

- the profile serializes daemon health into a stable JSON-safe payload;
- `health_check()` preserves the SDK health status;
- `readiness_check()` maps healthy to `ok`;
- degraded/unhealthy/stopped/unstarted map to `failed` by default;
- custom `ready_statuses` can include `degraded`;
- the profile can be plugged into `AsgiAgentApp(readiness_checks=...)`;
- public exports include the profile;
- runtime query loops remain free of A2A/push/health imports.
