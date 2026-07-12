# Distributed Session Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add concrete Redis lease and Postgres snapshot persistence adapters for durable web session hydration.

**Architecture:** Keep `DurableAgentSessionProvider` unchanged and add production adapters behind its existing `SessionLeaseStore` and `SessionPersistence` protocols. Tests use injected fake Redis/Postgres backends to validate adapter contracts without live services.

**Tech Stack:** Python 3.11, pytest, Redis command protocol, Postgres JSONB SQL.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-distributed-session-adapters-design.md`.

Target conclusion:

```text
Web distributed session support becomes production-credible only when the SDK
ships concrete distributed lease and snapshot adapters behind the same
DurableAgentSessionProvider lifecycle.
```

Deferred:

- live Redis/Postgres integration tests
- Lua atomic Redis release/refresh
- migration runner changes
- runtime loop integration
- ASGI routing changes

## File Structure

Modify:

- `src/agentos/channels/durable_session.py`
  Add `RedisSessionLeaseStore`.

- `src/agentos/channels/__init__.py`
  Export `RedisSessionLeaseStore`.

- `src/agentos/persistence/postgres.py`
  Add `PostgresSessionSnapshotPersistence`.

- `src/agentos/persistence/__init__.py`
  Export `PostgresSessionSnapshotPersistence`.

- `src/agentos/__init__.py`
  Export both adapters.

- `src/agentos/readiness.py`
  Update distributed session evidence/gaps.

- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/persistence.md`
- `docs/production-readiness.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
  Document the adapters without claiming full production completion.

Create:

- `tests/channels/test_redis_session_lease_store.py`
- `tests/persistence/test_postgres_session_snapshot_persistence.py`
- `docs/migrations/2026-06-12-postgres-session-snapshots.sql`

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`

## Task 1: Redis Session Lease Store

**Files:**
- Create: `tests/channels/test_redis_session_lease_store.py`
- Modify: `src/agentos/channels/durable_session.py`
- Modify: `src/agentos/channels/__init__.py`

- [x] **Step 1: Write failing Redis lease tests**

Tests should cover:

- `acquire()` writes Redis `SET NX PX` and returns owner/token.
- concurrent acquire with `wait_timeout_seconds=0` raises `SessionLeaseError`.
- stale release does not delete a newer token.
- `refresh()` preserves ownership and extends expiry.

- [x] **Step 2: Run Redis lease tests and verify failure**

Run:

```powershell
uv run pytest tests/channels/test_redis_session_lease_store.py -q
```

Expected: FAIL because `RedisSessionLeaseStore` is not defined.

- [x] **Step 3: Implement minimal Redis lease store**

Add the class to `durable_session.py` and export it from
`agentos.channels`.

- [x] **Step 4: Run Redis lease tests and verify pass**

Run:

```powershell
uv run pytest tests/channels/test_redis_session_lease_store.py -q
```

Expected: PASS.

## Task 2: Postgres Session Snapshot Persistence

**Files:**
- Create: `tests/persistence/test_postgres_session_snapshot_persistence.py`
- Modify: `src/agentos/persistence/postgres.py`
- Modify: `src/agentos/persistence/__init__.py`
- Create: `docs/migrations/2026-06-12-postgres-session-snapshots.sql`

- [x] **Step 1: Write failing Postgres persistence tests**

Tests should cover:

- saving and loading a real `SessionSnapshot`
- list/delete behavior
- migration contains `agentos_session_snapshots` and `payload JSONB`

- [x] **Step 2: Run Postgres persistence tests and verify failure**

Run:

```powershell
uv run pytest tests/persistence/test_postgres_session_snapshot_persistence.py -q
```

Expected: FAIL because `PostgresSessionSnapshotPersistence` is not defined.

- [x] **Step 3: Implement minimal Postgres persistence**

Add `PostgresSessionSnapshotPersistence` to `persistence/postgres.py`, export it
from `agentos.persistence`, and add migration SQL.

- [x] **Step 4: Run Postgres persistence tests and verify pass**

Run:

```powershell
uv run pytest tests/persistence/test_postgres_session_snapshot_persistence.py -q
```

Expected: PASS.

## Task 3: Public API, Readiness, And Docs

**Files:**
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify docs listed in File Structure.

- [x] **Step 1: Write failing public API assertions**

Assert top-level exports include `RedisSessionLeaseStore` and
`PostgresSessionSnapshotPersistence`.

- [x] **Step 2: Run public API test and verify failure**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py::test_distributed_session_adapter_public_api_exports -q
```

Expected: FAIL before top-level exports.

- [x] **Step 3: Export adapters and update readiness/docs**

Export the adapters and update docs to say the SDK now ships concrete Redis
lease and Postgres snapshot adapters, while production deployments still own
credentials, schema migration execution, TTL policy, failure recovery, and
workspace enforcement.

- [x] **Step 4: Run public/docs/readiness tests**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py tests/test_readiness.py tests/docs/test_production_readiness_docs.py -q
```

Expected: PASS.

## Task 4: Verification

- [x] Run focused tests:

```powershell
uv run pytest tests/channels/test_redis_session_lease_store.py tests/persistence/test_postgres_session_snapshot_persistence.py tests/channels/test_durable_session_provider.py -q
```

- [x] Run runtime boundary search:

```powershell
rg "RedisSessionLeaseStore|PostgresSessionSnapshotPersistence|Redis|Postgres|agentos.channels.durable_session|agentos.persistence.postgres" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Update this plan's checkboxes.

## Self-Review

- Spec coverage: Redis lease, Postgres snapshot persistence, migration, exports,
  readiness/docs, and runtime boundary are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: runtime loops must remain deployment-agnostic.
