# Distributed Web Session Operations Profile Boundary Design

## Target Conclusion

Distributed web agents already have durable session hydration primitives: a
`DistributedWebRuntimeProfile` can assemble a `DurableAgentSessionProvider`
with lease and snapshot adapters so any node can handle the next turn. The next
production gap is operational readiness, not another turn loop. The SDK should
expose a deployment-facing profile that states which distributed session
operations components are configured, which protections are SDK-owned, and
which remain deployment-owned.

## Current State

The SDK already owns:

- `DistributedWebRuntimeProfile`
- `DurableAgentSessionProvider`
- `SnapshotAgentFactory`
- `SessionLeaseStore`
- `RedisSessionLeaseStore`
- `SessionSnapshot`
- `SessionPersistence`
- `PostgresSessionSnapshotPersistence`
- session acquire, hydrate, save, and release lifecycle
- async channel offloading through `async_get_agent()` and
  `async_release_agent()`

These prove the key web-agent requirement: two different nodes can serve turns
for the same session when they share lease and snapshot backends. Production
deployments still need explicit TTL/stale lease recovery policy, migration
execution, credential and secret handling, auth/tenant policy, workspace
policy, live backend verification, rollout policy, and operational alerting.

## Proposed Boundary

Add `DistributedWebSessionOperationsProfile` in `agentos.runtime.profile`.

The profile:

- accepts configured deployment component names
- reports missing required operations components
- exposes `readiness_metadata()`
- exposes ASGI-compatible `readiness_check()`
- returns JSON-safe tuples, booleans, and strings
- documents SDK-owned and deployment-owned responsibilities

Required components:

- `durable_session_provider`
- `lease_store`
- `snapshot_persistence`
- `snapshot_migration`
- `lease_ttl_policy`
- `stale_lease_recovery`
- `credential_policy`
- `auth_tenant_policy`
- `workspace_policy`
- `live_backend_verification`

SDK-owned responsibilities:

- `DistributedWebRuntimeProfile`
- `DurableAgentSessionProvider`
- `SnapshotAgentFactory`
- `SessionLeaseStore`
- `RedisSessionLeaseStore`
- `SessionSnapshot`
- `SessionPersistence`
- `PostgresSessionSnapshotPersistence`
- `acquire/hydrate/save/release lifecycle`
- `async session provider offload`

Deployment-owned responsibilities:

- `Redis/Postgres credentials`
- `migration execution`
- `lease TTL tuning`
- `stale lease recovery policy`
- `auth and tenant integration`
- `workspace policy configuration`
- `live backend verification`
- `rollout and rollback policy`
- `alerting and incident response`

## Integration

`DistributedWebRuntimeProfile.readiness_metadata()` should include this profile
as the recommended operational readiness surface. The new profile does not
replace `DistributedWebRuntimeProfile`; it complements it by turning the
current `production_gaps` list into a structured readiness contract.

## Non-Goals

- No Redis or Postgres migration runner.
- No credential manager or secret distribution.
- No stale lease sweeper.
- No background recovery loop.
- No auth/tenant implementation.
- No workspace enforcement implementation.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Validation

- Runtime profile tests prove missing/ready metadata and input validation.
- Public API tests prove export from `agentos.runtime` and top-level `agentos`.
- Readiness/docs tests prove distributed web session readiness references the
  profile and still marks operational policy as deployment-owned.
- Runtime boundary scan proves the profile does not leak into query loops.
