# Team Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class LLM-callable team tools on top of `TeamRuntime`.

**Architecture:** Keep `TeamRuntime` as the state/message boundary and add `TeamTools` as a thin tool registration layer, mirroring `PlannerTools`. The tools serialize records to JSON and scope all operations to `owner_agent_id` without running worker sessions.

**Tech Stack:** Python 3.11 dataclasses, pytest, `ToolRegistry`, JSON tool handlers.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-team-tools-design.md`.

Target conclusion:

```text
Team discussion agents need first-class LLM-callable tools over TeamRuntime;
team records/messages alone are not enough for an SDK-level agent pattern.
```

Deferred:

- distributed `TeamStore`
- automatic worker session runner
- worker session factory
- planner/team auto-integration
- UI stream protocol

## File Structure

Modify:

- `src/agentos/multi/team.py`
  Add `TeamTools`.

- `src/agentos/multi/__init__.py`
  Export `TeamTools`.

- `src/agentos/__init__.py`
  Export `TeamTools`.

- `src/agentos/readiness.py`
  Update team-discussion evidence and app-glue gaps.

- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/multi-agent.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
- `docs/production-readiness.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
  Document team tools without claiming automatic worker sessions.

Modify tests:

- `tests/multi/test_team_tools.py`
- `tests/architecture/test_public_api.py`
- `tests/test_readiness.py`

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`

## Task 1: Team Tool Registration And Basic Flow

**Files:**
- Create: `tests/multi/test_team_tools.py`
- Modify: `src/agentos/multi/team.py`

- [x] **Step 1: Write failing registration/basic-flow tests**

Tests should cover:

- registered tool names:
  `team_create`, `agent_create`, `team_say`, `team_read_messages`,
  `team_delete`
- owner creates team as leader
- owner adds worker member
- owner sends directed message
- worker-scoped tools can read that message

- [x] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/multi/test_team_tools.py -q
```

Expected: FAIL because `TeamTools` is not defined.

- [x] **Step 3: Implement minimal `TeamTools`**

Add `TeamTools` to `team.py` with JSON serializers for team, member, and
message records.

- [x] **Step 4: Run tests and verify pass**

Run:

```powershell
uv run pytest tests/multi/test_team_tools.py -q
```

Expected: PASS.

## Task 2: Ownership And Mutation Boundaries

**Files:**
- Modify: `tests/multi/test_team_tools.py`
- Modify: `src/agentos/multi/team.py`

- [x] **Step 1: Write failing boundary tests**

Tests should cover:

- `team_say` ignores spoofed `from_agent_id`.
- `team_read_messages` does not expose sender-only invisible messages.
- `team_delete` raises `TeamMembershipError` when the owner is not a member.

- [x] **Step 2: Run boundary tests and verify failure**

Run:

```powershell
uv run pytest tests/multi/test_team_tools.py -q
```

Expected: FAIL before boundary implementation is complete.

- [x] **Step 3: Implement boundary checks**

Use `TeamRuntime.messages_for()` and an owner membership check before delete.

- [x] **Step 4: Run boundary tests and verify pass**

Run:

```powershell
uv run pytest tests/multi/test_team_tools.py -q
```

Expected: PASS.

## Task 3: Exports, Readiness, And Docs

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify docs listed in File Structure.

- [x] **Step 1: Write failing public API/readiness assertions**

Assert `TeamTools` is exported from `agentos.multi` and top-level `agentos`.
Assert team readiness evidence includes `TeamTools` and required app glue no
longer contains "team tools".

- [x] **Step 2: Run public/readiness tests and verify failure**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports tests/test_readiness.py::test_team_and_planner_gaps_are_explicit -q
```

Expected: FAIL before exports/readiness updates.

- [x] **Step 3: Export and update docs**

Update docs to say team tools exist, while worker session lifecycle,
distributed TeamStore, permission downgrade, and UI stream protocol remain
gaps.

- [x] **Step 4: Run public/readiness/docs tests**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py tests/test_readiness.py tests/docs/test_production_readiness_docs.py -q
```

Expected: PASS.

## Task 4: Verification

- [x] Run focused tests:

```powershell
uv run pytest tests/multi/test_team_tools.py tests/multi/test_team_runtime.py -q
```

- [x] Run runtime boundary search:

```powershell
rg "TeamTools|agentos.multi.team|TeamRuntime" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [x] Run `uv run python -m compileall -q src tests`.
- [ ] Run `uv run pytest -q`.
- [ ] Run `git diff --check`.
- [ ] Update this plan's checkboxes.

## Self-Review

- Spec coverage: registration, core tool flow, ownership boundaries, exports,
  readiness/docs, and runtime boundary are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: no worker session runner or runtime loop coupling.
