# Distributed Team Profile Preset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-oriented distributed team profile preset that assembles team runtime, worker runner, worker daemon, retry/cancellation stores, and UI stream boundaries.

**Architecture:** `DistributedTeamRuntimeProfile` lives with other runtime profiles and composes existing `agentos.multi.team` primitives through injected adapters. It exposes readiness metadata without starting worker threads or coupling `QueryLoop` to team concerns.

**Tech Stack:** Python dataclasses, existing team runtime primitives, pytest.

---

### Task 1: Profile Assembly Boundary

**Files:**
- Modify: `tests/runtime/test_runtime_profile.py`
- Modify: `src/agentos/runtime/profile.py`

- [ ] **Step 1: Write failing profile tests**

Add tests that instantiate `DistributedTeamRuntimeProfile` with in-memory team
adapters and a recording worker agent provider. Assert it builds a
`TeamRuntime`, `TeamWorkerRunner`, and `TeamWorkerDaemon`, then processes one
queued team message through `daemon.run_once()`.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests\runtime\test_runtime_profile.py -q`

Expected: fails because `DistributedTeamRuntimeProfile` does not exist.

- [ ] **Step 3: Implement minimal profile**

Add `DistributedTeamRuntimeProfile` to `src/agentos/runtime/profile.py`. It
must assemble `TeamRuntime`, optional `TeamWorkerRunner`, optional
`TeamWorkerDaemon`, expose build helpers, and raise `NotImplementedError` from
`build_agent(...)`.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests\runtime\test_runtime_profile.py -q`

Expected: all runtime profile tests pass.

### Task 2: Public API

**Files:**
- Modify: `src/agentos/runtime/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing public API assertions**

Assert `DistributedTeamRuntimeProfile` is exported from `agentos.runtime` and
top-level `agentos`.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests\architecture\test_public_api.py -q`

Expected: fails because the new profile is not exported.

- [ ] **Step 3: Export the profile**

Update runtime and top-level public exports.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests\architecture\test_public_api.py -q`

Expected: public API tests pass.

### Task 3: Readiness And Guidance

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Write failing readiness/doc assertions where available**

Run the existing readiness and docs tests before documentation updates.

- [ ] **Step 2: Update readiness source of truth**

Set team discussion recommended profile to
`DistributedTeamRuntimeProfile`, add profile evidence, and keep deployment
gaps explicit.

- [ ] **Step 3: Update docs and skill guidance**

Document the preset as the standard team discussion assembly path while keeping
OS/container sandboxing, migrations, process supervision, and worker scaling as
deployment-owned work.

- [ ] **Step 4: Verify docs/readiness**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\architecture\test_public_api.py -q
```

Expected: all targeted tests pass.

## Verification Commands

```powershell
uv run pytest tests\runtime\test_runtime_profile.py tests\multi\test_team_runtime.py tests\multi\test_team_worker_runner.py tests\multi\test_team_worker_daemon.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```
