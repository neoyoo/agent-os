# Team UI Network Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a replayable team UI event HTTP endpoint and Postgres-backed distributed UI stream store.

**Architecture:** `TeamUiStreamStore` remains the SDK boundary. `AsgiAgentApp` gets optional store injection and a JSON replay route. `PostgresTeamUiStreamStore` lives beside other team Postgres adapters and uses the existing UI event serializer helpers.

**Tech Stack:** Python dataclasses/protocols, ASGI, JSON, parameterized SQL, pytest fake connections.

---

## Files

- Modify: `src/agentos/channels/asgi.py`
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/multi/postgres_team.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Create: `docs/migrations/2026-06-15-postgres-team-ui-events.sql`
- Modify: `tests/channels/test_asgi_app.py`
- Create: `tests/multi/test_postgres_team_ui_stream_store.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/*`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Task 1: Endpoint Tests

- [ ] Add ASGI tests for replay, missing store, invalid cursor, and invalid limit.
- [ ] Run targeted tests and verify RED.

## Task 2: Postgres Store Tests

- [ ] Add fake-connection tests for append/list/migration coverage.
- [ ] Run targeted tests and verify RED.

## Task 3: Implementation

- [ ] Add optional `team_ui_stream` and limit config to `AsgiAgentApp`.
- [ ] Add route matcher for `/v1/teams/{team_id}/ui-events`.
- [ ] Serialize replay response with `team_ui_event_to_dict()`.
- [ ] Add `PostgresTeamUiStreamStore`.
- [ ] Add migration.
- [ ] Export public symbols.

## Task 4: Docs And Verification

- [ ] Update readiness/docs/skills/roadmap to move network JSON replay endpoint and distributed store into primitives.
- [ ] Keep live SSE/follow and tool sandbox as remaining gaps.
- [ ] Verify runtime loop boundary has no `TeamUi` imports.
- [ ] Run focused tests, compileall, full pytest, and `git diff --check`.
