# AgentOS Migration Index

Canonical path: `docs/migrations/README.md`.

This migration index is Phase 100: Release Hardening evidence. It lists the
schema/data migrations that may be needed by deployment-owned stores. AgentOS
keeps migration docs and reference SQL/Python files in the SDK repository, but
deployment-owned execution applies them to real Postgres, SQLite, Qdrant, or
other backend environments.

Every SQL migration should include `migrate:up` and `migrate:down` sections
when rollback is meaningful. Python migration scripts should describe the
required environment variables and idempotency behavior.

## Index

| File | Backend | Purpose |
|------|---------|---------|
| `0.2-single-async-query-loop.md` | SDK API | Breaking migration to the single async QueryLoop and `agentos.sync` adapter. |
| `2026-05-07-postgres-agent-registry.sql` | Postgres | Agent registry metadata. |
| `2026-05-07-postgres-memory-backends.sql` | Postgres | Durable memory backend tables. |
| `2026-05-07-qdrant-recall-collection.py` | Qdrant | Recall vector collection creation. |
| `2026-05-07-sqlite-session-persistence.sql` | SQLite | Local session persistence schema. |
| `2026-05-16-postgres-multi-agent-tasks.sql` | Postgres | Multi-agent task truth store. |
| `2026-06-12-postgres-session-snapshots.sql` | Postgres | Session snapshot persistence. |
| `2026-06-12-postgres-team-store.sql` | Postgres | Team state store. |
| `2026-06-12-postgres-team-worker-retries.sql` | Postgres | Team worker retry state. |
| `2026-06-15-postgres-a2a-push-notifications.sql` | Postgres | A2A push notification config and delivery state. |
| `2026-06-15-postgres-plan-store.sql` | Postgres | Planner plan truth store. |
| `2026-06-15-postgres-team-ui-events.sql` | Postgres | Team UI replay/follow events. |
| `2026-06-15-postgres-team-worker-cancellations.sql` | Postgres | Team worker cancellation intent state. |
| `2026-06-16-postgres-plan-claims.sql` | Postgres | Planner claim/lease state. |

## Ownership

SDK-owned:

- migration index
- reference schema files
- public API references to stores that require these migrations
- docs that state which adapter uses which migration

Deployment-owned:

- credential management
- migration ordering in each environment
- backups, restore tests, and rollout windows
- running migrations in CI/CD, staging, and production
- rollback execution and release approval

