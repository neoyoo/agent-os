# Team Worker Persistent Retry Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Postgres-backed `TeamWorkerRetryStore` so team worker retry/backoff state can survive restarts and be shared across nodes.

**Architecture:** Implement `PostgresTeamWorkerRetryStore` in the team Postgres adapter module. Store `TeamWorkerRetryRecord` payloads as JSONB and use existing `AgentEnvelope` serializers to persist optional delivery data.

**Tech Stack:** Python dataclasses/protocol adapter style, psycopg-compatible connection protocol, JSONB migrations, pytest fake connections.

---

## Files

- Modify: `src/agentos/multi/postgres_team.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Create: `tests/multi/test_postgres_team_worker_retry_store.py`
- Create: `docs/migrations/2026-06-12-postgres-team-worker-retries.sql`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Task 1: Failing Persistent Retry Store Tests

- [ ] **Step 1: Create `tests/multi/test_postgres_team_worker_retry_store.py`**

Cover record round-trip, list ordering, clear, SQL upsert shape, and migration
contents.

- [ ] **Step 2: Run focused test**

Run:

```powershell
uv run pytest tests\multi\test_postgres_team_worker_retry_store.py -q
```

Expected: import failure for `PostgresTeamWorkerRetryStore`.

## Task 2: Implement Adapter And Migration

- [ ] **Step 1: Add `PostgresTeamWorkerRetryStore`**

Implement in `src/agentos/multi/postgres_team.py` with `record_failure`,
`get`, `list_records`, `clear`, `close`, and JSON helpers.

- [ ] **Step 2: Add migration**

Create `docs/migrations/2026-06-12-postgres-team-worker-retries.sql` with
`-- migrate:up` and `-- migrate:down`.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_postgres_team_worker_retry_store.py -q
```

Expected: focused tests pass.

## Task 3: Public API And Docs

- [ ] **Step 1: Export public symbol**

Add `PostgresTeamWorkerRetryStore` to lazy multi exports and top-level exports,
then update public API tests.

- [ ] **Step 2: Update readiness/docs/skill guidance**

Mark persistent retry store as available for team discussion. Keep cancellation
scheduling, permission downgrade, and UI stream protocol as gaps.

- [ ] **Step 3: Run public/readiness/docs tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: selected tests pass.

## Task 4: Full Verification

- [ ] **Step 1: Runtime boundary search**

Run:

```powershell
rg "PostgresTeamWorkerRetryStore|TeamWorkerRetry" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [ ] **Step 2: Full verification**

Run:

```powershell
uv run python -m compileall -q src tests
uv run pytest -q
git diff --check
```

Expected: compileall passes, pytest passes, diff check has no errors.

## Self-Review

- Spec coverage: persistent adapter, migration, exports, docs, readiness, and
  runtime boundary checks are covered.
- Placeholder scan: no TBD/TODO placeholders are used.
- Type consistency: persistent adapter is consistently named
  `PostgresTeamWorkerRetryStore`.
