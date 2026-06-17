# Team UI Stream Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stable team UI event protocol and in-memory stream store so UIs can replay team and worker events without coupling to internal team runtime objects.

**Architecture:** Add pure data/event primitives in `src/agentos/multi/team.py`, serializer helpers in `src/agentos/multi/serializers.py`, and optional `ui_stream` injection into `TeamRuntime` and `TeamWorkerRunner`. The first store is in-memory and bounded; network/SSE and distributed persistence remain later slices.

**Tech Stack:** Python dataclasses, Protocols, `threading.RLock`, JSON-safe serializer helpers, pytest.

---

## Files

- Modify: `src/agentos/multi/team.py`
- Modify: `src/agentos/multi/serializers.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Create: `tests/multi/test_team_ui_stream.py`
- Modify: `tests/multi/test_team_runtime.py`
- Modify: `tests/multi/test_team_worker_runner.py`
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

## Task 1: Failing UI Stream Tests

- [ ] **Step 1: Add stream store and serializer tests**

Add tests for:

- append/list cursor behavior
- bounded in-memory stream retention
- `TeamUiEvent` serializer round trip

- [ ] **Step 2: Add runtime publication tests**

Add tests proving `TeamRuntime` emits `team_created`, `member_added`,
`message_appended`, and `team_deleted` when `ui_stream` is configured.

- [ ] **Step 3: Add runner result publication tests**

Add tests proving `TeamWorkerRunner.publish_ui_results()` or equivalent emits
completed, failed, retry-skipped, and cancelled worker run events.

- [ ] **Step 4: Run focused tests to verify RED**

Run:

```powershell
uv run pytest tests\multi\test_team_ui_stream.py tests\multi\test_team_runtime.py tests\multi\test_team_worker_runner.py -q
```

Expected: import/type failures for missing UI stream symbols before
implementation.

## Task 2: Implement UI Event Primitives

- [ ] **Step 1: Add event dataclass/protocol/store**

Add `TeamUiEventKind`, `TeamUiEvent`, `TeamUiStreamStore`, and
`InMemoryTeamUiStreamStore` to `src/agentos/multi/team.py`.

- [ ] **Step 2: Add serializers**

Add `team_ui_event_to_dict()` and `team_ui_event_from_dict()` to
`src/agentos/multi/serializers.py`.

- [ ] **Step 3: Run stream tests**

Run:

```powershell
uv run pytest tests\multi\test_team_ui_stream.py -q
```

Expected: stream tests pass.

## Task 3: Wire Runtime And Runner

- [ ] **Step 1: Wire TeamRuntime**

Add optional `ui_stream` to `TeamRuntime`. Emit events after successful team
create/member add/message append/delete operations.

- [ ] **Step 2: Wire TeamWorkerRunner**

Add optional `ui_stream` and publish worker result events after `run_pending()`
or through a small explicit helper that daemon/services can call.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_team_runtime.py tests\multi\test_team_worker_runner.py tests\multi\test_team_ui_stream.py -q
```

Expected: focused tests pass.

## Task 4: Public API, Docs, Full Verification

- [ ] **Step 1: Export public symbols**

Add UI stream types to multi and top-level exports plus public API tests.

- [ ] **Step 2: Update readiness/docs/skills**

Mark UI stream protocol as SDK primitive while keeping network/SSE endpoint and
distributed UI stream persistence as app-owned/future.

- [ ] **Step 3: Runtime boundary search**

Run:

```powershell
rg "TeamUi|TeamUI|UiStream" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
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

- Spec coverage: event model, cursor replay, bounded storage, serialization,
  runtime publication, runner result publication, exports, docs, and runtime
  boundary checks are covered.
- Placeholder scan: no TBD/TODO placeholders are used.
- Type consistency: UI stream symbols consistently use the `TeamUi*` prefix.
