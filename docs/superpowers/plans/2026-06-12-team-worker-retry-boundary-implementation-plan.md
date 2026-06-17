# Team Worker Retry Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 16A retry/backoff primitives for team worker continuations so daemonized workers do not hot-loop failed deliveries.

**Architecture:** Extend `TeamWorkerRunner` with optional retry policy and retry store boundaries. Keep queue transports unchanged; the runner owns retry gating and can retry stored local deliveries while production deployments can later replace the in-memory store with a durable adapter.

**Tech Stack:** Python dataclasses, Protocols, `threading.RLock`, pytest, existing agent-os team worker primitives.

---

## Files

- Modify: `src/agentos/multi/team.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `tests/multi/test_team_worker_runner.py`
- Modify: `tests/multi/test_team_worker_daemon.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Task 1: Failing Retry Tests

- [ ] **Step 1: Add retry tests to `tests/multi/test_team_worker_runner.py`**

Cover scheduled retry, backoff skip, due retry success, and exhausted attempts.

- [ ] **Step 2: Add daemon retry state test**

Update `tests/multi/test_team_worker_daemon.py` so daemon state exposes retry
records from the runner.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_team_worker_runner.py tests\multi\test_team_worker_daemon.py -q
```

Expected: import or attribute failures for retry symbols before implementation.

## Task 2: Implement Retry Boundary

- [ ] **Step 1: Modify `src/agentos/multi/team.py`**

Add `TeamWorkerRetryPolicy`, `TeamWorkerRetryRecord`,
`TeamWorkerRetryStatus`, `TeamWorkerRetryStore`, and
`InMemoryTeamWorkerRetryStore`.

- [ ] **Step 2: Wire retry into `TeamWorkerRunner`**

Add optional `retry_policy`, `retry_store`, and `clock`. Process due retry
records before new queue deliveries, skip records still in backoff, clear on
success, and mark exhausted after max attempts.

- [ ] **Step 3: Expose retry records through daemon state**

Add `retry_records` to `TeamWorkerDaemonState` and populate it from
`runner.retry_records()` when available.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_team_worker_runner.py tests\multi\test_team_worker_daemon.py -q
```

Expected: all focused tests pass.

## Task 3: Public API And Docs

- [ ] **Step 1: Export public symbols**

Add retry symbols to `src/agentos/multi/__init__.py`, `src/agentos/__init__.py`,
and `tests/architecture/test_public_api.py`.

- [ ] **Step 2: Update readiness and docs**

Update readiness matrix, public production docs, skill docs, and roadmap so
team discussion includes retry/backoff primitives but still names persistent
retry store, cancellation, permission, and UI stream as gaps.

- [ ] **Step 3: Run API/readiness/docs tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all selected tests pass.

## Task 4: Full Verification

- [ ] **Step 1: Runtime boundary search**

Run:

```powershell
rg "TeamWorkerRetry|TeamWorkerDaemon" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
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

- Spec coverage: retry policy, retry store, backoff skip, due retry, exhaustion,
  daemon state, exports, docs, and runtime boundary checks are covered.
- Placeholder scan: no TBD/TODO placeholders are used.
- Type consistency: retry symbols are consistently named with the
  `TeamWorkerRetry*` prefix.
