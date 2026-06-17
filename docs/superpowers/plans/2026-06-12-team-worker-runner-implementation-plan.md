# Team Worker Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a runner boundary that turns queued team-message wakeups into worker continuation turns.

**Architecture:** Keep team execution orchestration inside `agentos.multi.team` and depend only on `TeamWorkerSessionProvider`, `AgentMessageQueue`, and a new `TeamWorkerAgentProvider` protocol. The runner consumes one batch of deliveries, runs worker continuations, acks successful team messages, and records failures without importing runtime loops.

**Tech Stack:** Python 3.11 dataclasses, Protocols, pytest, existing `AgentMessageQueue` and `Agent.run_continuation()`.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-team-worker-runner-design.md`.

Target conclusion:

```text
A team worker session is only useful if a distributed runner can turn team
message wakeups into worker continuation turns. The SDK should provide this
runner boundary without coupling QueryLoop to team semantics.
```

Deferred:

- background daemon dispatcher
- async runner
- retry/backoff scheduler
- cancellation handling
- durable worker-session adapter
- permission enforcement beyond workspace/session handles

## File Structure

Modify:

- `src/agentos/multi/team.py`
  Add `TeamWorkerAgentProvider`, `TeamWorkerRunResult`,
  `TeamWorkerRunError`, and `TeamWorkerRunner`.

- `src/agentos/multi/__init__.py`
  Export runner types.

- `src/agentos/__init__.py`
  Export runner types.

- `tests/multi/test_team_worker_runner.py`
  New tests for runner behavior.

- `tests/architecture/test_public_api.py`
  Assert public exports.

- `src/agentos/readiness.py`
- `tests/test_readiness.py`
- `docs/production-readiness.md`
- `.claude/skills/agent-os/SKILL.md`
- `.claude/skills/agent-os/flow/01-requirements.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/multi-agent.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
  Update production guidance without claiming retry/cancel/daemon policy.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`

## Task 1: Runner Behavior

**Files:**
- Create: `tests/multi/test_team_worker_runner.py`
- Modify: `src/agentos/multi/team.py`

- [x] **Step 1: Write failing tests**

Cover:

- `TeamWorkerRunner.run_pending()` executes a worker continuation for a pending
  `team_message` delivery.
- Successful deliveries are acked.
- `task_request` deliveries are ignored and not acked.
- Worker continuation failures are recorded and not acked.

- [x] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/multi/test_team_worker_runner.py -q
```

Expected: FAIL because runner types do not exist.

- [x] **Step 3: Implement minimal runner**

Add the four runner types and implement `run_pending(team_id=None)` with batch
semantics.

- [x] **Step 4: Run tests and verify pass**

Run:

```powershell
uv run pytest tests/multi/test_team_worker_runner.py -q
```

Expected: PASS.

## Task 2: Public API, Readiness, And Docs

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify docs and skill files listed in File Structure.

- [x] **Step 1: Write failing public/readiness assertions**

Assert:

- `TeamWorkerRunner`, `TeamWorkerAgentProvider`, `TeamWorkerRunResult`, and
  `TeamWorkerRunError` are exported.
- required app glue does not contain `worker execution runner`.
- required app glue includes retry/backoff policy, cancellation scheduling, and
  permission policy at Phase 14A. Later Phase 16A adds retry/backoff primitives.
- team concurrency evidence includes `TeamWorkerRunner`.

- [x] **Step 2: Run public/readiness tests and verify failure**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports tests/test_readiness.py::test_team_and_planner_gaps_are_explicit -q
```

Expected: FAIL before exports/readiness updates.

- [x] **Step 3: Export and update docs**

Update docs to say the SDK has a runner boundary for one batch of team-message
continuations. Later Phase 15A adds the daemon host loop; retry/backoff,
cancel, and permission enforcement remain separate policy work.

- [x] **Step 4: Run public/readiness/docs tests**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py tests/test_readiness.py tests/docs/test_production_readiness_docs.py -q
```

Expected: PASS.

## Task 3: Verification

- [x] Run focused tests:

```powershell
uv run pytest tests/multi/test_team_worker_runner.py tests/multi/test_team_runtime.py tests/multi/test_team_tools.py -q
```

- [x] Run runtime boundary search:

```powershell
rg "TeamWorkerRunner|TeamWorkerAgentProvider|TeamWorkerRunResult|TeamWorkerRunError" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Update this plan's checkboxes.

## Self-Review

- Spec coverage: team-message execution, ack behavior, ignored non-team
  envelopes, failure recording, exports, readiness/docs, and runtime isolation
  are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: daemon/retry/cancel/permission policy remain deferred.
