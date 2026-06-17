# Team Worker Cancellation Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a team worker cancellation intent boundary so queued and retry-delayed worker continuations can be skipped before execution.

**Architecture:** Extend `TeamWorkerRunner` with an optional cancellation store. The runner checks cancellation records before invoking worker `run_continuation()`, acknowledges exact delivery cancels, keeps worker-scope cancels active, and exposes cancellation records through daemon state.

**Tech Stack:** Python dataclasses, Protocols, `threading.RLock`, pytest, existing team worker runner/daemon tests.

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
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Task 1: Failing Cancellation Tests

- [ ] **Step 1: Add runner tests**

Add tests proving delivery-level cancel skips and acks queued delivery,
worker-level cancel stays requested, and cancel clears retry-store deliveries.

- [ ] **Step 2: Add daemon state test**

Add a test proving daemon state exposes cancellation records.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_team_worker_runner.py tests\multi\test_team_worker_daemon.py -q
```

Expected: import failures for cancellation symbols before implementation.

## Task 2: Implement Cancellation Boundary

- [ ] **Step 1: Add cancellation dataclasses/store**

Add `TeamWorkerCancellationRecord`, `TeamWorkerCancellationStatus`,
`TeamWorkerCancellationStore`, and `InMemoryTeamWorkerCancellationStore`.

- [ ] **Step 2: Wire runner**

Add optional `cancellation_store`. Check cancellation before queued and retry
deliveries call `run_continuation()`.

- [ ] **Step 3: Wire daemon state**

Expose cancellation records through `TeamWorkerDaemonState`.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_team_worker_runner.py tests\multi\test_team_worker_daemon.py -q
```

Expected: focused tests pass.

## Task 3: Public API And Docs

- [ ] **Step 1: Export public symbols**

Add cancellation types to multi and top-level exports and public API tests.

- [ ] **Step 2: Update readiness/docs/skills**

Mark cancellation scheduling as an SDK primitive. Keep permission downgrade and
UI stream protocol as gaps.

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
rg "TeamWorkerCancellation|TeamWorkerDaemon|TeamWorkerRetry" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
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

- Spec coverage: delivery cancel, worker cancel, retry cancel, daemon state,
  exports, docs, and runtime boundary checks are covered.
- Placeholder scan: no TBD/TODO placeholders are used.
- Type consistency: cancellation symbols are consistently named with the
  `TeamWorkerCancellation*` prefix.
