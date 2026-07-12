# Distributed Session Adapters Design (Phase 8A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 2A durable session provider and Phase 7 readiness matrix

## Target Conclusion

```text
Web distributed session support becomes production-credible only when the SDK
ships concrete distributed lease and snapshot adapters behind the same
DurableAgentSessionProvider lifecycle.
```

`DurableAgentSessionProvider` already defines the right lifecycle:
acquire lease, load snapshot, run turn, save snapshot, release lease. Until the
lease and snapshot boundaries have production adapters, web distributed agents
remain an in-memory proof rather than a reusable SDK deployment shape.

## Scope

Add two small concrete adapters:

- `RedisSessionLeaseStore` implementing `SessionLeaseStore`
- `PostgresSessionSnapshotPersistence` implementing `SessionPersistence`

The adapters follow existing agent-os patterns:

- accept injected fake clients/connections for tests
- lazily import optional Redis/Postgres dependencies only when no injected
  backend is supplied
- raise clear backend-unavailable errors through existing SDK error types
- keep `QueryLoop` and `AsyncQueryLoop` unaware of deployment details

## Redis Lease Semantics

`RedisSessionLeaseStore` stores one key per session:

```text
<key_prefix>:session:lease:<session_id>
```

Acquire:

- generate a unique token
- write a JSON payload containing `owner_id`, `token`, and `expires_at`
- use Redis `SET key payload NX PX <ttl_ms>`
- if the key is held, wait until `wait_timeout_seconds` expires
- return `SessionLease` only after Redis confirms ownership

Release:

- read the current payload
- delete only when the stored token matches the lease token
- stale releases are ignored

Refresh:

- read the current payload
- fail if the token no longer owns the lease
- rewrite the same token with a new TTL

This first slice does not require Lua scripting. That means release/refresh are
protocol-correct for injected fake tests and acceptable as an SDK boundary, but
production deployments may later swap in an atomic Lua implementation behind
the same class without changing caller code.

## Postgres Snapshot Semantics

`PostgresSessionSnapshotPersistence` stores full `SessionSnapshot` payloads in
one table:

```text
agentos_session_snapshots(
  session_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

The adapter:

- serializes through `session_snapshot_to_dict`
- deserializes through `session_snapshot_from_dict`
- uses `ON CONFLICT` upsert for save
- preserves `SnapshotVersionError`
- converts JSON corruption/shape errors to `SnapshotLoadError`

## Non-Goals

- No live Redis/Postgres integration tests.
- No Lua atomic release script in this slice.
- No migration runner changes.
- No runtime loop integration.
- No change to ASGI request routing.

## Acceptance Criteria

- Redis lease store rejects concurrent acquire, ignores stale release, and
  allows reacquire after release.
- Redis lease store refresh preserves ownership and extends the expiry.
- Postgres snapshot persistence saves and loads a real `SessionSnapshot`.
- Postgres snapshot persistence supports list/delete.
- A migration file defines the snapshot table.
- Public exports include the new adapters.
- Readiness/docs mention concrete Redis lease and Postgres snapshot adapters.
- Runtime loops remain free of Redis/Postgres/session adapter imports.
