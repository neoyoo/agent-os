# A2A Task Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add A2A protocol task lookup and cancel operations by projecting internal `TaskStore` state into protocol-facing `A2ATask` values.

**Architecture:** Extend the Phase 11A `a2a_operations` boundary with task lifecycle runner/client/server methods. Keep `/a2a/tasks` as the internal task bridge; expose protocol lifecycle routes as `/a2a/tasks/{task_id}` and `/a2a/tasks/{task_id}:cancel`.

**Tech Stack:** Python 3.11 dataclasses/protocols, pytest, existing ASGI and TaskStore primitives.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-a2a-task-lifecycle-design.md`.

Target conclusion:

```text
message/send starts an A2A interaction, but production interoperability also
needs task lookup and cancel operations. agent-os should project internal
TaskStore state into A2A task lifecycle semantics through a protocol boundary,
not by exposing the internal /a2a/tasks bridge as full A2A.
```

Deferred:

- task resubscription
- streaming task updates
- push notification configuration
- signed cards and trust store
- per-peer auth enforcement
- external conformance tests

## File Structure

Modify:

- `src/agentos/channels/a2a_operations.py`
  Add task lifecycle mapping, runner protocol, TaskStore runner, server/client
  get/cancel methods.

- `src/agentos/channels/asgi.py`
  Add `GET /a2a/tasks/{task_id}` and `POST /a2a/tasks/{task_id}:cancel` routes
  when `a2a_operations` is configured.

- `src/agentos/channels/__init__.py`
- `src/agentos/__init__.py`
- `tests/architecture/test_public_api.py`
  Export task lifecycle boundary types.

- `tests/channels/test_a2a_operations.py`
  Add mapping, server, and client tests.

- `tests/channels/test_asgi_app.py`
  Add ASGI route tests.

- `src/agentos/readiness.py`
- `docs/production-readiness.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/multi-agent.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
  Update docs while preserving remaining gaps.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- Existing `/a2a/tasks` internal bridge behavior.

## Task 1: Task Projection And Runner

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`
- Modify: `src/agentos/channels/a2a_operations.py`

- [x] **Step 1: Write failing task projection tests**

Cover:

- `a2a_task_from_task_record()` maps queued/running/completed/failed/cancelled/timeout.
- terminal result summary becomes an agent message.
- running cancel intent sets metadata `cancelRequested`.
- `TaskStoreA2ATaskLifecycleRunner.get_task()` returns a projection.
- `cancel_task()` cancels queued task and returns canceled projection.

- [x] **Step 2: Run projection tests and verify failure**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py::test_a2a_task_from_task_record_maps_internal_statuses tests/channels/test_a2a_operations.py::test_task_store_a2a_lifecycle_runner_gets_and_cancels_task -q
```

Expected: FAIL until projection and runner exist.

- [x] **Step 3: Implement projection and runner**

Add:

- `A2ATaskLifecycleRunner`
- `TaskStoreA2ATaskLifecycleRunner`
- `a2a_task_from_task_record(record)`
- `a2a_state_from_task_status(status)`

- [x] **Step 4: Run projection tests and verify pass**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py::test_a2a_task_from_task_record_maps_internal_statuses tests/channels/test_a2a_operations.py::test_task_store_a2a_lifecycle_runner_gets_and_cancels_task -q
```

Expected: PASS.

## Task 2: Server And Client Task Lifecycle Methods

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`
- Modify: `src/agentos/channels/a2a_operations.py`

- [x] **Step 1: Write failing server/client tests**

Cover:

- `A2AOperationServer.handle_task_get(task_id)` returns task response.
- `handle_task_cancel(task_id)` returns canceled/working response.
- missing task returns error `-32001`.
- non-cancelable task returns error `-32002`.
- `A2AOperationClient.get_task()` calls `<card.url>/tasks/{id}`.
- `A2AOperationClient.cancel_task()` calls `<card.url>/tasks/{id}:cancel`.

- [x] **Step 2: Run server/client tests and verify failure**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py::test_a2a_operation_server_handles_task_get_and_cancel tests/channels/test_a2a_operations.py::test_a2a_operation_server_returns_task_lifecycle_errors tests/channels/test_a2a_operations.py::test_a2a_operation_client_gets_and_cancels_task -q
```

Expected: FAIL until server/client methods exist.

- [x] **Step 3: Implement server/client lifecycle methods**

Extend `A2AOperationServer` and `A2AOperationClient`.

- [x] **Step 4: Run operation tests and verify pass**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py -q
```

Expected: PASS.

## Task 3: ASGI Task Lifecycle Routes

**Files:**
- Modify: `tests/channels/test_asgi_app.py`
- Modify: `src/agentos/channels/asgi.py`

- [x] **Step 1: Write failing ASGI route tests**

Cover:

- `GET /a2a/tasks/{task_id}` returns task response.
- `POST /a2a/tasks/{task_id}:cancel` returns cancel response.
- routes return 404 when `a2a_operations` is missing.
- `/a2a/tasks` internal bridge still behaves as before.

- [x] **Step 2: Run ASGI route tests and verify failure**

Run:

```powershell
uv run pytest tests/channels/test_asgi_app.py::test_asgi_app_routes_a2a_task_get_and_cancel tests/channels/test_asgi_app.py::test_asgi_app_returns_404_for_missing_a2a_task_lifecycle -q
```

Expected: FAIL until routes exist.

- [x] **Step 3: Implement ASGI routes**

Add path matcher for protocol task lifecycle routes.

- [x] **Step 4: Run ASGI route tests and verify pass**

Run:

```powershell
uv run pytest tests/channels/test_asgi_app.py::test_asgi_app_routes_a2a_task tests/channels/test_asgi_app.py::test_asgi_app_routes_a2a_task_get_and_cancel tests/channels/test_asgi_app.py::test_asgi_app_returns_404_for_missing_a2a_task_lifecycle -q
```

Expected: PASS.

## Task 4: Public API, Readiness, Docs

**Files:**
- Modify files listed in File Structure.

- [x] **Step 1: Write failing API/readiness assertions**

Assert exports include:

- `A2ATaskLifecycleRunner`
- `TaskStoreA2ATaskLifecycleRunner`
- `a2a_task_from_task_record`
- `a2a_state_from_task_status`

Assert readiness evidence includes task get/cancel while preserving
streaming/push/auth gaps.

- [x] **Step 2: Run API/readiness tests and verify failure**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py::test_public_api_uses_responsibility_specific_names tests/test_readiness.py::test_a2a_discovery_does_not_claim_operation_parity -q
```

Expected: FAIL until exports/readiness are updated.

- [x] **Step 3: Export and update docs**

Update public exports and docs. Keep language precise: task lookup/cancel exist,
full A2A compliance does not.

- [x] **Step 4: Run docs/readiness tests and verify pass**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py tests/test_readiness.py tests/docs/test_production_readiness_docs.py -q
```

Expected: PASS.

## Task 5: Verification

- [x] Run focused tests:

```powershell
uv run pytest tests/channels/test_a2a_operations.py tests/channels/test_a2a_adapter.py tests/channels/test_a2a_server.py tests/channels/test_a2a_card.py tests/channels/test_asgi_app.py tests/architecture/test_public_api.py tests/test_readiness.py -q
```

- [x] Run runtime boundary search:

```powershell
rg "A2AOperation|A2ATaskLifecycle|TaskStoreA2A|agentos.channels" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Update this plan's checkboxes.

## Self-Review

- Spec coverage: task get/cancel, internal state projection, ASGI routes,
  exports, readiness/docs, and runtime boundary checks are covered.
- Placeholder scan: no placeholders remain.
- Boundary check: existing `/a2a/tasks` remains internal; protocol lifecycle
  routes are separate.
