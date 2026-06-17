# Team Worker Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 15A `TeamWorkerDaemon` so team worker continuation runners can be hosted as a long-running service loop.

**Architecture:** Implement a narrow daemon wrapper in `src/agentos/multi/team.py` that owns background polling lifecycle and state snapshots while delegating all worker delivery behavior to `TeamWorkerRunner`. Keep retry/backoff, cancellation, permission enforcement, and UI streaming outside this phase.

**Tech Stack:** Python dataclasses, `threading.Thread`, `threading.Event`, pytest, existing agent-os multi-agent primitives.

---

## Files

- Modify: `src/agentos/multi/team.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Create: `tests/multi/test_team_worker_daemon.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Task 1: Add Failing Daemon Tests

- [ ] **Step 1: Create `tests/multi/test_team_worker_daemon.py`**

Add tests for `run_once()`, background start/stop/join, failure visibility, and
runtime-loop boundary.

- [ ] **Step 2: Run the focused daemon test**

Run:

```powershell
uv run pytest tests\multi\test_team_worker_daemon.py -q
```

Expected: import failure for `TeamWorkerDaemon` because implementation does not
exist yet.

## Task 2: Implement `TeamWorkerDaemon`

- [ ] **Step 1: Modify `src/agentos/multi/team.py`**

Add:

- `TeamWorkerDaemonStatus`
- `TeamWorkerDaemonState`
- `TeamWorkerDaemon`

Use `threading.Event` for shutdown and `threading.Thread` for the service loop.

- [ ] **Step 2: Run daemon tests**

Run:

```powershell
uv run pytest tests\multi\test_team_worker_daemon.py -q
```

Expected: all daemon tests pass.

## Task 3: Export Public API

- [ ] **Step 1: Modify public exports**

Add daemon symbols to `src/agentos/multi/__init__.py` and `src/agentos/__init__.py`.

- [ ] **Step 2: Update API tests**

Add daemon symbols to `tests/architecture/test_public_api.py`.

- [ ] **Step 3: Run API tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 4: Update Readiness And Docs

- [ ] **Step 1: Modify readiness matrix**

Update `team-discussion` evidence to include `TeamWorkerDaemon`. Required app
glue should keep retry/backoff policy, cancellation scheduling, permission
downgrade policy, and UI stream protocol at Phase 15A. Later Phase 16A adds
retry/backoff primitives.

- [ ] **Step 2: Modify readiness tests**

Update assertions in `tests/test_readiness.py` to verify daemon evidence and
that retry/backoff policy and cancellation scheduling remain gaps at Phase 15A.

- [ ] **Step 3: Update public docs and skill docs**

Update production readiness docs and agent-os skill modules so team discussion
is described as daemon-capable but not retry/cancel production complete at
Phase 15A.

- [ ] **Step 4: Run docs/readiness tests**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: readiness/docs tests pass.

## Task 5: Verify Boundaries And Full Suite

- [ ] **Step 1: Check runtime loop boundary**

Run:

```powershell
rg "TeamWorkerDaemon|TeamWorkerDaemonState|TeamWorkerDaemonStatus" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [ ] **Step 2: Run focused multi-agent tests**

Run:

```powershell
uv run pytest tests\multi\test_team_worker_daemon.py tests\multi\test_team_worker_runner.py tests\multi\test_team_runtime.py tests\multi\test_team_tools.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run full verification**

Run:

```powershell
uv run python -m compileall -q src tests
uv run pytest -q
git diff --check
```

Expected: compileall passes, pytest passes, diff check has no errors.

## Self-Review

- Spec coverage: daemon lifecycle, state, exports, docs, readiness, and runtime
  loop boundary are covered by the tasks above.
- Placeholder scan: no TBD/TODO placeholders are used.
- Type consistency: daemon symbols are consistently named
  `TeamWorkerDaemon`, `TeamWorkerDaemonState`, and `TeamWorkerDaemonStatus`.
