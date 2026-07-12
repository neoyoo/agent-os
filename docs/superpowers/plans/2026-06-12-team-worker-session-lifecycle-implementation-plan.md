# Team Worker Session Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit team worker session lifecycle boundary so `agent_create` can create/register worker sessions before storing team members.

**Architecture:** Keep `TeamRuntime` as the team state coordinator and add provider protocols/types inside `agentos.multi.team`. The runtime optionally calls the provider when adding worker members and closes worker sessions when a team is deleted, while worker execution/wakeup dispatch remains a later phase.

**Tech Stack:** Python 3.11 dataclasses, Protocols, pytest, existing `TeamRuntime`/`TeamTools` APIs.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-team-worker-session-lifecycle-design.md`.

Target conclusion:

```text
Team workers are independent sessions, not only member records. agent-os needs
an explicit worker session lifecycle boundary before team discussion can become
a production-grade distributed agent pattern.
```

Deferred:

- automatic worker wakeup dispatcher
- worker run loop
- retry/cancel scheduler
- durable worker-session adapter
- permission enforcement beyond carrying workspace handles

## File Structure

Modify:

- `src/agentos/multi/team.py`
  Add lifecycle dataclasses/protocol/provider and wire `TeamRuntime`.

- `src/agentos/multi/__init__.py`
  Export lifecycle types.

- `src/agentos/__init__.py`
  Export lifecycle types.

- `tests/multi/test_team_runtime.py`
  Add worker lifecycle tests.

- `tests/multi/test_team_tools.py`
  Add `agent_create` session id test.

- `tests/architecture/test_public_api.py`
  Assert public exports.

- `src/agentos/readiness.py`
- `tests/test_readiness.py`
- `docs/production-readiness.md`
- `.claude/skills/agent-os/SKILL.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/multi-agent.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
  Update guidance to distinguish session lifecycle from execution runner.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`

## Task 1: Worker Session Lifecycle Runtime Boundary

**Files:**
- Modify: `tests/multi/test_team_runtime.py`
- Modify: `src/agentos/multi/team.py`

- [x] **Step 1: Write failing tests**

Cover:

- worker member creation calls `InMemoryTeamWorkerSessionProvider`
- generated session id is stored on `TeamMemberRecord`
- explicit `session_id` is honored
- leader member creation does not create a worker session
- deleting a team closes active worker sessions

- [x] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/multi/test_team_runtime.py -q
```

Expected: FAIL because lifecycle types and provider wiring do not exist.

- [x] **Step 3: Implement minimal lifecycle boundary**

Add:

- `TeamWorkerSessionStatus`
- `TeamWorkerSessionRequest`
- `TeamWorkerSession`
- `TeamWorkerSessionProvider`
- `InMemoryTeamWorkerSessionProvider`

Wire `TeamRuntime.add_member()` and `TeamRuntime.delete_team()`.

- [x] **Step 4: Run tests and verify pass**

Run:

```powershell
uv run pytest tests/multi/test_team_runtime.py -q
```

Expected: PASS.

## Task 2: TeamTools And Public API

**Files:**
- Modify: `tests/multi/test_team_tools.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/multi/team.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`

- [x] **Step 1: Write failing tool/export tests**

Cover:

- `agent_create` returns provider-generated `session_id`
- `agentos.multi` exports lifecycle types
- top-level `agentos` exports lifecycle types

- [x] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/multi/test_team_tools.py tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports -q
```

Expected: FAIL before tool/export updates.

- [x] **Step 3: Update tool behavior and exports**

Keep the `agent_create` tool name and input schema. It should return the
runtime-created member record, including generated `session_id`.

- [x] **Step 4: Run tests and verify pass**

Run:

```powershell
uv run pytest tests/multi/test_team_tools.py tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports -q
```

Expected: PASS.

## Task 3: Readiness And Documentation

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify docs and skill files listed in File Structure.

- [x] **Step 1: Write failing readiness assertions**

Assert:

- `worker session lifecycle` is no longer required app glue
- required app glue includes `worker execution runner`
- session-state evidence includes `TeamWorkerSessionProvider`

- [x] **Step 2: Run readiness test and verify failure**

Run:

```powershell
uv run pytest tests/test_readiness.py::test_team_and_planner_gaps_are_explicit -q
```

Expected: FAIL before readiness update.

- [x] **Step 3: Update readiness/docs**

Explain that SDK now has a lifecycle boundary, while automatic worker wakeup,
execution, retry/backoff policy, cancellation scheduling, UI stream protocol,
and permission policy remain production gaps. Later phases add worker runner,
daemon, and retry/backoff primitives.

- [x] **Step 4: Run readiness/docs tests**

Run:

```powershell
uv run pytest tests/test_readiness.py tests/docs/test_production_readiness_docs.py -q
```

Expected: PASS.

## Task 4: Verification

- [x] Run focused tests:

```powershell
uv run pytest tests/multi/test_team_runtime.py tests/multi/test_team_tools.py tests/multi/test_postgres_team_store.py -q
```

- [x] Run runtime boundary search:

```powershell
rg "TeamWorkerSession|TeamWorkerSessionProvider|InMemoryTeamWorkerSessionProvider" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Update this plan's checkboxes.

## Self-Review

- Spec coverage: lifecycle request/session/provider, runtime wiring, tool
  output, exports, readiness/docs, and runtime isolation are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: execution runner and scheduler remain explicitly deferred.
