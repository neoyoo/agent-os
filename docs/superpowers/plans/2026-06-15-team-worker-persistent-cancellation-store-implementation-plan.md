# Team Worker Persistent Cancellation Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Postgres-backed `TeamWorkerCancellationStore` so team worker cancellation intent survives restarts and is visible across nodes.

**Architecture:** Reuse the existing cancellation store protocol. Add `PostgresTeamWorkerCancellationStore` beside `PostgresTeamWorkerRetryStore` in `src/agentos/multi/postgres_team.py`, storing a JSONB payload plus indexed identity/status columns. No `QueryLoop` or runner semantics change is required.

**Tech Stack:** Python dataclasses, existing Postgres protocol facades, JSON serializers, pytest fake connection tests, SQL migration docs.

---

## Files

- Modify: `src/agentos/multi/postgres_team.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Create: `docs/migrations/2026-06-15-postgres-team-worker-cancellations.sql`
- Create: `tests/multi/test_postgres_team_worker_cancellation_store.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Task 1: Failing Store Tests

- [ ] **Step 1: Add fake Postgres tests**

Add tests proving:

- request/get via `match()` round-trips delivery-scoped records
- exact delivery/message cancels match before worker-scope cancels
- acknowledged and cleared records no longer match
- list filters by team and orders deterministically

- [ ] **Step 2: Add migration test**

Assert the migration includes up/down paths, the table name, uniqueness over
nullable identity fields, JSONB payload, active indexes, and down drop.

- [ ] **Step 3: Run focused tests to verify RED**

Run:

```powershell
uv run pytest tests\multi\test_postgres_team_worker_cancellation_store.py -q
```

Expected: import failure for missing `PostgresTeamWorkerCancellationStore`.

## Task 2: Implement Store And Migration

- [ ] **Step 1: Add migration**

Create `docs/migrations/2026-06-15-postgres-team-worker-cancellations.sql` with
table, indexes, and down path.

- [ ] **Step 2: Add Postgres store**

Implement request, match, acknowledge, clear, and list methods in
`src/agentos/multi/postgres_team.py`.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_postgres_team_worker_cancellation_store.py -q
```

Expected: focused tests pass.

## Task 3: Public API And Readiness

- [ ] **Step 1: Export public symbol**

Add `PostgresTeamWorkerCancellationStore` to lazy multi exports, top-level
exports, and public API tests.

- [ ] **Step 2: Update readiness**

Move persistent cancellation storage from required app glue into SDK evidence.
Keep UI stream and tool sandbox enforcement as gaps.

- [ ] **Step 3: Run public/readiness docs tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: selected tests pass.

## Task 4: Skill Docs, Roadmap, Full Verification

- [ ] **Step 1: Update SDK skill guidance**

Mention `PostgresTeamWorkerCancellationStore` in team discussion guidance and
spec generation. Remove persistent cancellation storage from current team gaps.

- [ ] **Step 2: Update roadmap**

Append Phase 19A artifacts and conclusion; remaining team gaps become UI stream
protocol and tool sandbox enforcement.

- [ ] **Step 3: Runtime boundary search**

Run:

```powershell
rg "PostgresTeamWorkerCancellation|TeamWorkerCancellation" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [ ] **Step 4: Full verification**

Run:

```powershell
uv run python -m compileall -q src tests
uv run pytest -q
git diff --check
```

Expected: compileall passes, pytest passes, diff check has no errors.

## Self-Review

- Spec coverage: request, match specificity, acknowledge, clear, list,
  migration, exports, docs, and runtime boundary checks are covered.
- Placeholder scan: no TBD/TODO placeholders are used.
- Type consistency: persistent cancellation symbols consistently use
  `PostgresTeamWorkerCancellationStore`.
