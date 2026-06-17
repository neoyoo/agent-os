# Distributed Web Profile Design (Phase 10A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 1 runtime profiles, Phase 2A durable session provider,
> Phase 8A Redis/Postgres distributed session adapters

## Target Conclusion

```text
Distributed web agents need an SDK profile preset that assembles durable
session, distributed lease, snapshot persistence, workspace, and channel policy;
raw adapters alone still leave production users wiring too much by hand.
```

agent-os now has the important primitives for dynamic context hydration across
nodes: `DurableAgentSessionProvider`, `RedisSessionLeaseStore`, and
`PostgresSessionSnapshotPersistence`. The next SDK-level step is a production
profile preset that makes the intended composition explicit and testable.

## Scope

Add `DistributedWebRuntimeProfile` in `agentos.runtime.profile`.

The profile should:

- extend the existing web-channel profile semantics
- build a `DurableAgentSessionProvider` from supplied dependencies
- default `session_lifecycle` to `durable-session`
- expose `readiness_metadata()` that names the concrete adapters and production
  policy gaps
- build an `AsgiAgentApp` through the existing channel boundary
- keep `QueryLoop` and `AsyncQueryLoop` deployment-agnostic

## Constructor Shape

The preset should accept:

- `agent_factory`
- `lease_store`
- `snapshot_persistence`
- optional `owner_id`
- optional `lease_ttl_seconds`
- optional `acquire_timeout_seconds`
- optional `auth_policy`
- optional `rate_limiter`
- optional `workspace_provider`
- optional `workspace_request`
- optional `readiness_checks`
- optional `health_checks`
- optional `a2a_server`

`owner_id` should default to a stable SDK-friendly value such as
`agentos-web-node`, while still being overrideable by production deployments.

## Readiness Metadata

`readiness_metadata()` should include:

- `session_lifecycle: durable-session`
- `session_provider: DurableAgentSessionProvider`
- `lease_store: <class name>`
- `snapshot_persistence: <class name>`
- `workspace` metadata when present
- `production_gaps` listing auth/tenant policy, workspace enforcement,
  migration rollout, TTL/recovery policy, and live backend verification

The metadata is not a health check. It is an inspectable profile declaration for
docs, readiness endpoints, and generated specs.

## Non-Goals

- No live Redis/Postgres connection tests.
- No new ASGI routes.
- No migration runner.
- No automatic secret/credential management.
- No QueryLoop/AsyncQueryLoop changes.

## Acceptance Criteria

- The profile assembles a `DurableAgentSessionProvider`.
- Two profile instances sharing fake lease/persistence dependencies can hydrate
  the same session across nodes.
- `build_channel_app()` returns `AsgiAgentApp`.
- `readiness_metadata()` names the provider, lease store, persistence adapter,
  and production gaps.
- Public exports include `DistributedWebRuntimeProfile`.
- Readiness/docs point web distributed specs to this profile preset.
- Runtime loops remain free of distributed adapter imports.
