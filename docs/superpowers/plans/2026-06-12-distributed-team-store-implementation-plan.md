# Distributed Team Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Postgres-backed `TeamStore` adapter so team discussion state can be shared across nodes.

**Architecture:** Keep `TeamRuntime` unchanged and add `PostgresTeamStore` beside `PostgresTaskStore`. Serialize team/member/message dataclasses to JSONB payloads while keeping queryable identity/status columns and deterministic read ordering.

**Tech Stack:** Python 3.11 dataclasses, pytest, psycopg-compatible connection protocol, Postgres JSONB migrations.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-distributed-team-store-design.md`.

Target conclusion:

```text
Team agents are persistent leader/worker session boundaries; a distributed
TeamStore is the minimum SDK-owned state primitive for multi-node teams.
```

Deferred:

- automatic worker session lifecycle
- worker scheduler
- planner/team auto-integration
- UI stream protocol
- row-level authorization

## File Structure

Create:

- `src/agentos/multi/postgres_team.py`
  Postgres-backed implementation of `TeamStore`.

- `tests/multi/test_postgres_team_store.py`
  Fake-connection tests for team/member/message round trips and visibility.

- `docs/migrations/2026-06-12-postgres-team-store.sql`
  Postgres schema for team records, members, and messages.

Modify:

- `src/agentos/multi/serializers.py`
  Add serializers for `TeamRecord`, `TeamMemberRecord`, and `WorkspaceHandle`.

- `src/agentos/multi/__init__.py`
  Lazy-export `PostgresTeamStore`.

- `src/agentos/__init__.py`
  Top-level export for `PostgresTeamStore`.

- `tests/architecture/test_public_api.py`
  Assert public exports.

- `src/agentos/readiness.py`
  Update team-discussion evidence and remaining gaps.

- `tests/test_readiness.py`
  Update readiness assertions.

- `docs/production-readiness.md`
- `.claude/skills/agent-os/SKILL.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/multi-agent.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
  Update production guidance without claiming worker sessions are automatic.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`

## Task 1: Serializers And Store Behavior Tests

**Files:**
- Modify: `src/agentos/multi/serializers.py`
- Create: `tests/multi/test_postgres_team_store.py`

- [x] **Step 1: Write failing tests**

Cover:

- team/member/message round trip through `PostgresTeamStore`
- workspace handles round-trip on team and member records
- `mark_team_deleted()` changes team status and prevents future member/message writes
- `list_messages()` preserves in-memory visibility semantics
- migration file contains the expected tables and indexes

- [x] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/multi/test_postgres_team_store.py -q
```

Expected: FAIL because `PostgresTeamStore` and new serializers do not exist.

## Task 2: Implement PostgresTeamStore

**Files:**
- Create: `src/agentos/multi/postgres_team.py`
- Modify: `src/agentos/multi/serializers.py`
- Create: `docs/migrations/2026-06-12-postgres-team-store.sql`

- [x] **Step 1: Add minimal serializers**

Add:

- `workspace_handle_to_dict`
- `workspace_handle_from_dict`
- `team_record_to_dict`
- `team_record_from_dict`
- `team_member_record_to_dict`
- `team_member_record_from_dict`

- [x] **Step 2: Add store implementation**

Implement the existing `TeamStore` methods with SQL following
`PostgresTaskStore` conventions:

- constructor with `dsn`, `connection`, and `pool`
- `from_pool()`
- `_execute()`, `_commit()`, `_json_dump()`, `_json_value()`, `close()`

- [x] **Step 3: Add migration**

Create the three tables and deterministic indexes:

- `agentos_team_records`
- `agentos_team_members`
- `agentos_team_messages`

- [x] **Step 4: Run focused tests**

Run:

```powershell
uv run pytest tests/multi/test_postgres_team_store.py -q
```

Expected: PASS.

## Task 3: Public API, Readiness, And Docs

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify docs listed in File Structure.

- [x] **Step 1: Write failing public/readiness assertions**

Assert:

- `agentos.multi.PostgresTeamStore` exists
- `agentos.PostgresTeamStore` exists
- team readiness evidence includes `PostgresTeamStore`
- required app glue does not include `distributed TeamStore`
- worker lifecycle remains required app glue

- [x] **Step 2: Run public/readiness tests and verify failure**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports tests/test_readiness.py::test_team_and_planner_gaps_are_explicit -q
```

Expected: FAIL before exports/readiness updates.

- [x] **Step 3: Export and update docs**

Update the public API and docs to position `PostgresTeamStore` as the
distributed team state adapter. Keep worker session lifecycle, permission
downgrade, and UI stream protocol as remaining gaps.

- [x] **Step 4: Run public/readiness/docs tests**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py tests/test_readiness.py tests/docs/test_production_readiness_docs.py -q
```

Expected: PASS.

## Task 4: Verification

- [x] Run focused tests:

```powershell
uv run pytest tests/multi/test_postgres_team_store.py tests/multi/test_team_runtime.py tests/multi/test_team_tools.py -q
```

- [x] Run runtime boundary search:

```powershell
rg "PostgresTeamStore|postgres_team|TeamStore" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Update this plan's checkboxes.

## Self-Review

- Spec coverage: distributed team storage, workspace serialization, visibility,
  migration, public API, readiness, docs, and runtime isolation are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: worker session lifecycle remains explicitly deferred.
