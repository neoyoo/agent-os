# A2A Task Resubscribe Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned `tasks/resubscribe` operation boundary and route A2A task SSE subscription through it.

**Architecture:** Keep operation semantics in `agentos.channels.a2a_operations`. `A2AOperationServer` authorizes/rate-limits `tasks/resubscribe` before reading task lifecycle state, then returns one task subscription event projection. `AsgiAgentApp` keeps the SSE loop but asks `A2AOperationServer` for each next event so REST/SSE and JSON-RPC subscription share the same boundary.

**Tech Stack:** Python dataclasses/protocols, existing A2A operation server, ASGI SSE helper, pytest.

Spec: `docs/superpowers/specs/2026-06-16-a2a-task-resubscribe-boundary-design.md`

---

### Task 1: RED Tests For Operation-Level Resubscribe

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] Add a test for `A2AOperationRequest.task_resubscribe(...)` and `A2AOperationResponse(task_event=...)` round-trip.
- [ ] Add a test for `A2AOperationServer.handle_operation(...)` with method `tasks/resubscribe`, params `id`, and `afterEventId`.
- [ ] Add a test proving operation/resource auth and per-peer rate-limit keys use operation `tasks/resubscribe`, task id, resource type `task`, and resource id.
- [ ] Run `uv run pytest tests\channels\test_a2a_operations.py -q` and confirm RED failure on the missing API.

### Task 2: Implement A2A Operation Primitives

**Files:**
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] Add `A2AOperationRequest.task_resubscribe(...)`.
- [ ] Add `task_event: A2ATaskSubscriptionEvent | None = None` to `A2AOperationResponse`.
- [ ] Serialize/deserialize `task_event` through `result.statusUpdate` using existing `a2a_task_subscription_event_to_dict/from_dict`.
- [ ] Add `A2AOperationServer.handle_task_resubscribe(...)` with version, extension, auth, rate-limit, lifecycle, missing-task, and empty-result handling.
- [ ] Add JSON-RPC `tasks/resubscribe` dispatch in `handle_operation(...)`.
- [ ] Run `uv run pytest tests\channels\test_a2a_operations.py -q` and confirm GREEN.

### Task 3: Route ASGI Subscribe Through The Boundary

**Files:**
- Modify: `tests/channels/test_asgi_app.py`
- Modify: `src/agentos/channels/asgi.py`

- [ ] Add a RED test where `POST /a2a/tasks/{id}:subscribe` with a static bearer inbound policy and no Authorization header returns a JSON unauthorized error instead of opening SSE.
- [ ] Add a RED test where the same route consumes a `tasks/resubscribe` rate-limit key and returns a JSON rate-limit error before opening SSE.
- [ ] Change `_handle_a2a_task_subscribe(...)` to call `A2AOperationServer.handle_task_resubscribe(...)` for each cursor step.
- [ ] Preserve existing SSE success behavior and invalid cursor errors.
- [ ] Run `uv run pytest tests\channels\test_asgi_app.py tests\channels\test_a2a_operations.py -q`.

### Task 4: Public API, Readiness, Docs, Skill, Roadmap

**Files:**
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] Add public API expectations if new public names are introduced.
- [ ] Update readiness evidence so A2A operation support includes `tasks/resubscribe`.
- [ ] Update production readiness and skill guidance to say task subscription now runs through the A2A operation boundary.
- [ ] Add Phase 56 to the roadmap with target conclusion, artifacts, and conclusion.
- [ ] Run docs/readiness/public API tests.

### Task 5: Final Verification

**Files:**
- No new edits unless verification exposes a real defect.

- [ ] Run targeted tests:

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_asgi_app.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] Run full tests:

```powershell
uv run pytest -q
```

- [ ] Run compile:

```powershell
uv run python -m compileall -q src tests
```

- [ ] Run runtime boundary scan:

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code `1` with no output.

- [ ] Run diff hygiene:

```powershell
git diff --check
```

Expected: exit code `0`; CRLF warnings are acceptable on this branch.
