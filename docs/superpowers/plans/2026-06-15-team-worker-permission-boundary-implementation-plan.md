# Team Worker Permission Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-level team worker permission policy that prevents worker sessions from receiving broader workspaces or undeclared capabilities.

**Architecture:** Keep the boundary inside the worker session provider path. `TeamWorkerPermissionPolicy` resolves the final worker workspace and validates workspace/capability narrowing before `InMemoryTeamWorkerSessionProvider` stores a session; `TeamRuntime.add_member()` naturally fails before persisting the member when the provider rejects the request.

**Tech Stack:** Python dataclasses, `pathlib.Path`, existing `WorkspaceHandle`/`WorkspacePolicy`, pytest, public API/readiness/docs tests.

---

## Files

- Modify: `src/agentos/multi/team.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/multi/test_team_runtime.py`
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

## Task 1: Failing Permission Tests

- [ ] **Step 1: Add worker permission tests**

Add tests in `tests/multi/test_team_runtime.py` for:

- broader requested worker scope is rejected
- worker root outside team root is rejected
- missing requested workspace defaults to team workspace
- policy allow-list rejects unexpected capabilities
- failed worker session creation does not persist the team member

- [ ] **Step 2: Run focused tests to verify RED**

Run:

```powershell
uv run pytest tests\multi\test_team_runtime.py -q
```

Expected: failures for missing `TeamWorkerPermissionPolicy` /
`TeamWorkerPermissionError`.

## Task 2: Implement Permission Policy

- [ ] **Step 1: Add policy and error**

Add `TeamWorkerPermissionError` and `TeamWorkerPermissionPolicy` in
`src/agentos/multi/team.py`.

- [ ] **Step 2: Wire provider**

Add optional `permission_policy` to `InMemoryTeamWorkerSessionProvider` and use
it to resolve workspace and validate capabilities before storing sessions.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_team_runtime.py -q
```

Expected: focused tests pass.

## Task 3: Public API And Readiness

- [ ] **Step 1: Export public symbols**

Add new policy/error symbols to `agentos.multi`, top-level `agentos`, and public
API tests.

- [ ] **Step 2: Update readiness matrix**

Move team discussion permission downgrade from `required_app_glue` into SDK
evidence. Keep tool sandbox enforcement and UI stream protocol as gaps.

- [ ] **Step 3: Run public/readiness docs tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: selected tests pass.

## Task 4: Skill Docs, Roadmap, Full Verification

- [ ] **Step 1: Update SDK skill guidance**

Update agent forms, multi-agent, requirements, and spec-generation guidance so
team specs mention `TeamWorkerPermissionPolicy` while still requiring app-owned
tool sandbox enforcement.

- [ ] **Step 2: Update roadmap**

Append Phase 18A artifacts and conclusion; reduce remaining team gaps.

- [ ] **Step 3: Runtime boundary search**

Run:

```powershell
rg "TeamWorkerPermission|WorkspacePolicy" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
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

- Spec coverage: workspace scope, workspace root, default workspace, capability
  allow-list, persistence failure behavior, exports, docs, and runtime boundary
  checks are covered.
- Placeholder scan: no TBD/TODO placeholders are used.
- Type consistency: permission symbols consistently use the
  `TeamWorkerPermission*` prefix.
