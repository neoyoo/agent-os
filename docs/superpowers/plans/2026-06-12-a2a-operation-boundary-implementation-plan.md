# A2A Operation Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal A2A protocol operation boundary for `message/send` without promoting the existing internal `/a2a/tasks` bridge to full A2A compliance.

**Architecture:** Add `agentos.channels.a2a_operations` beside the existing card and internal task bridge modules. The new module owns protocol-facing message/task/envelope models, operation client/server, and an Agent-backed runner. `AsgiAgentApp` exposes `/a2a/message:send` only when an operation server is configured.

**Tech Stack:** Python 3.11 dataclasses, protocols, pytest, existing ASGI/channel transport patterns.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-a2a-operation-boundary-design.md`.

Target conclusion:

```text
A2A Agent Card and discovery are only the interoperability entry point.
agent-os needs a protocol operation boundary that separates external A2A
message/task semantics from the internal AgentCoordinator task bridge.
```

Deferred:

- `message/stream`
- task lookup/cancel/resubscribe routes
- push notification configuration
- signed cards and trust store
- per-peer auth enforcement
- full external conformance tests

## File Structure

Create:

- `src/agentos/channels/a2a_operations.py`
  Protocol-facing A2A message/task/envelope models, operation runner, operation
  server, and operation client.

- `tests/channels/test_a2a_operations.py`
  Serialization, client, server, and Agent runner tests.

Modify:

- `src/agentos/channels/asgi.py`
  Add optional `a2a_operations` server and `/a2a/message:send` route.

- `src/agentos/channels/__init__.py`
  Export operation boundary types.

- `src/agentos/__init__.py`
  Export operation boundary types.

- `tests/channels/test_asgi_app.py`
  Cover `/a2a/message:send`.

- `tests/architecture/test_public_api.py`
  Cover public exports.

- `src/agentos/readiness.py`
  Add `A2AOperationServer` / `A2AOperationClient` evidence while preserving
  full-operation gaps.

- `docs/production-readiness.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/multi-agent.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
  Document the new boundary and remaining gaps.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- Existing `/a2a/tasks` behavior.

## Task 1: Protocol Models And Serialization

**Files:**
- Create: `tests/channels/test_a2a_operations.py`
- Create: `src/agentos/channels/a2a_operations.py`

- [x] **Step 1: Write failing serialization tests**

Cover:

- `A2AMessage` serializes role, parts, message id, context id, task id.
- `A2ATask` serializes task id, context id, state, and messages.
- `A2AOperationRequest.message_send(message)` emits method `message/send`.
- `A2AOperationResponse` round-trips result and error payloads.

- [x] **Step 2: Run model tests and verify failure**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py::test_a2a_message_and_task_round_trip tests/channels/test_a2a_operations.py::test_a2a_operation_request_and_response_round_trip -q
```

Expected: FAIL because `agentos.channels.a2a_operations` does not exist.

- [x] **Step 3: Implement protocol models**

Implement frozen dataclasses:

- `A2AMessagePart`
- `A2AMessage`
- `A2ATask`
- `A2AOperationError`
- `A2AOperationRequest`
- `A2AOperationResponse`

Add `*_to_dict` / `*_from_dict` helpers as methods or functions.

- [x] **Step 4: Run model tests and verify pass**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py::test_a2a_message_and_task_round_trip tests/channels/test_a2a_operations.py::test_a2a_operation_request_and_response_round_trip -q
```

Expected: PASS.

## Task 2: Operation Server And Agent Runner

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`
- Modify: `src/agentos/channels/a2a_operations.py`

- [x] **Step 1: Write failing server/runner tests**

Cover:

- `AgentA2AOperationRunner` converts a user text message into `Agent.run()`
  and returns a completed task with assistant message.
- `A2AOperationServer.handle_message_send()` parses payload and returns a
  response envelope.
- invalid payload returns `-32602`.
- unsupported method returns `-32601`.

- [x] **Step 2: Run server tests and verify failure**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py::test_agent_a2a_operation_runner_handles_message_send tests/channels/test_a2a_operations.py::test_a2a_operation_server_handles_message_send tests/channels/test_a2a_operations.py::test_a2a_operation_server_returns_protocol_errors -q
```

Expected: FAIL until server/runner exist.

- [x] **Step 3: Implement server and runner**

Add:

- `A2AOperationRunner` protocol
- `AgentA2AOperationRunner`
- `A2AOperationServer.handle_operation(payload, headers=None)`
- `A2AOperationServer.handle_message_send(payload, headers=None)`

Use `use_incoming_trace_headers(headers)` around runner execution.

- [x] **Step 4: Run server tests and verify pass**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py -q
```

Expected: PASS.

## Task 3: Operation Client And ASGI Route

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`
- Modify: `tests/channels/test_asgi_app.py`
- Modify: `src/agentos/channels/a2a_operations.py`
- Modify: `src/agentos/channels/asgi.py`

- [x] **Step 1: Write failing client and ASGI tests**

Cover:

- `A2AOperationClient.send_message(card, message)` posts to
  `<card.url>/message:send`.
- `AsgiAgentApp` returns operation response for `POST /a2a/message:send`
  when `a2a_operations` is configured.
- route returns 404 when operation server is not configured.

- [x] **Step 2: Run client/ASGI tests and verify failure**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py::test_a2a_operation_client_posts_message_send tests/channels/test_asgi_app.py::test_asgi_app_routes_a2a_message_send tests/channels/test_asgi_app.py::test_asgi_app_returns_404_for_missing_a2a_operations -q
```

Expected: FAIL until client/route exist.

- [x] **Step 3: Implement client and route**

Add:

- `A2AOperationClient`
- optional `a2a_operations` constructor argument on `AsgiAgentApp`
- route `POST /a2a/message:send`

Do not change `/a2a/tasks`.

- [x] **Step 4: Run client/ASGI tests and verify pass**

Run:

```powershell
uv run pytest tests/channels/test_a2a_operations.py tests/channels/test_asgi_app.py -q
```

Expected: PASS.

## Task 4: Public API, Readiness, Docs

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: docs listed in File Structure.

- [x] **Step 1: Write failing public API/readiness assertions**

Assert exports include:

- `A2AMessagePart`
- `A2AMessage`
- `A2ATask`
- `A2AOperationRequest`
- `A2AOperationResponse`
- `A2AOperationError`
- `A2AOperationRunner`
- `AgentA2AOperationRunner`
- `A2AOperationServer`
- `A2AOperationClient`

Assert readiness evidence mentions `A2AOperationServer` and keeps streaming,
push, and auth gaps.

- [x] **Step 2: Run public/readiness tests and verify failure**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py::test_public_api_uses_responsibility_specific_names tests/test_readiness.py::test_a2a_discovery_does_not_claim_operation_parity -q
```

Expected: FAIL until exports/readiness updates land.

- [x] **Step 3: Export and update docs**

Update public exports and docs. Keep language precise: `message/send` boundary
exists, full A2A compliance does not.

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
rg "A2AOperation|A2AAdapter|A2AServerAdapter|agentos.channels" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Update this plan's checkboxes.

## Self-Review

- Spec coverage: message/send protocol boundary, internal bridge separation,
  ASGI route, exports, readiness/docs, and runtime boundary checks are covered.
- Placeholder scan: no placeholders remain.
- Boundary check: existing `/a2a/tasks` remains internal; new operation code is
  isolated in `a2a_operations.py`.
