# Phase 8 Channels + Remote Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inbound HTTP/SSE/A2A channel adapters and remote A2A dispatch while preserving QueryLoop and message-store boundaries.

**Architecture:** `channels/` maps external request shapes to the public `Agent` facade and existing stream serializers. `multi/remote.py` bridges endpoint-backed `AgentCard` dispatch through the existing outbound `A2AAdapter`, and `AgentCoordinator` remains the single coordinator for local and remote experts.

**Tech Stack:** Python 3.11 dataclasses/protocols, standard-library JSON, minimal ASGI callable, existing `Agent`, `TaskTable`, `A2AAdapter`, and pytest fakes.

---

## File Structure

- Create `src/agentos/channels/types.py`: channel request/result/error dataclasses.
- Create `src/agentos/channels/auth.py`: `ChannelAuthPolicy` and `AllowAllChannelAuthPolicy`.
- Create `src/agentos/channels/session.py`: `AgentSessionProvider` and `InMemoryAgentSessionProvider`.
- Create `src/agentos/channels/http.py`: JSON turn request/response mapping.
- Create `src/agentos/channels/sse.py`: SSE turn request mapping using `event_to_sse()`.
- Create `src/agentos/channels/a2a_server.py`: inbound A2A payload adapter and default task runner.
- Create `src/agentos/channels/asgi.py`: minimal ASGI routing and response writer.
- Create `src/agentos/multi/remote.py`: `RemoteTaskExecutor`.
- Modify `src/agentos/multi/coordinator.py`: add remote dispatch branch.
- Modify `src/agentos/channels/__init__.py`: export new channel names.
- Modify `src/agentos/multi/__init__.py`: export `RemoteTaskExecutor`.
- Modify `src/agentos/__init__.py`: export public Phase 8 channel names.
- Modify `tests/architecture/test_public_api.py`: public API and drift assertions.

## Task 1: Channel Types, Auth, And Session Provider

**Files:**
- Create: `src/agentos/channels/types.py`
- Create: `src/agentos/channels/auth.py`
- Create: `src/agentos/channels/session.py`
- Test: `tests/channels/test_session_provider.py`

- [ ] **Step 1: Write failing tests**

Test `InMemoryAgentSessionProvider` creates an agent on first access, reuses it
for the same session id, creates a different agent for a different session id,
and records `release_agent()` calls without destroying the cached agent.

- [ ] **Step 2: Run red test**

Run:

```bash
uv run pytest tests/channels/test_session_provider.py -q
```

Expected: import failure for `agentos.channels.session`.

- [ ] **Step 3: Implement minimal types/auth/session**

Implement `ChannelTurnRequest`, `ChannelTurnResult`, `ChannelError`,
`ChannelAuthError`, `AllowAllChannelAuthPolicy`, `AgentSessionProvider`, and
`InMemoryAgentSessionProvider`.

- [ ] **Step 4: Run green test**

Run:

```bash
uv run pytest tests/channels/test_session_provider.py -q
```

Expected: all tests pass.

## Task 2: HTTP JSON Channel

**Files:**
- Create: `src/agentos/channels/http.py`
- Test: `tests/channels/test_http_channel.py`

- [ ] **Step 1: Write failing tests**

Cover a successful JSON turn, invalid JSON returning 400, missing message
returning 400, agent exception returning 500, and release hook execution after
a turn.

- [ ] **Step 2: Run red test**

Run:

```bash
uv run pytest tests/channels/test_http_channel.py -q
```

Expected: import failure for `agentos.channels.http`.

- [ ] **Step 3: Implement HTTP channel**

Implement `HttpAgentChannel.handle_turn(session_id, body)` returning
`ChannelTurnResult`.

- [ ] **Step 4: Run green test**

Run:

```bash
uv run pytest tests/channels/test_http_channel.py -q
```

Expected: all tests pass.

## Task 3: SSE Channel

**Files:**
- Create: `src/agentos/channels/sse.py`
- Test: `tests/channels/test_sse_channel.py`

- [ ] **Step 1: Write failing tests**

Cover streaming a turn as SSE chunks, filtering thinking when
`show_thinking=False`, and releasing the session after stream consumption.

- [ ] **Step 2: Run red test**

Run:

```bash
uv run pytest tests/channels/test_sse_channel.py -q
```

Expected: import failure for `agentos.channels.sse`.

- [ ] **Step 3: Implement SSE channel**

Implement `SseAgentChannel.stream_turn(session_id, body)` yielding existing
`event_to_sse()` chunks.

- [ ] **Step 4: Run green test**

Run:

```bash
uv run pytest tests/channels/test_sse_channel.py -q
```

Expected: all tests pass.

## Task 4: Inbound A2A Server Adapter

**Files:**
- Create: `src/agentos/channels/a2a_server.py`
- Test: `tests/channels/test_a2a_server.py`

- [ ] **Step 1: Write failing tests**

Cover `AgentA2ATaskRunner` wrapping `Agent.run()`, `A2AServerAdapter.handle_task`
returning TaskResult JSON, invalid payload returning failed TaskResult JSON, and
`handle_health()` returning `{"status": "ok"}`.

- [ ] **Step 2: Run red test**

Run:

```bash
uv run pytest tests/channels/test_a2a_server.py -q
```

Expected: import failure for `agentos.channels.a2a_server`.

- [ ] **Step 3: Implement A2A server adapter**

Implement `A2ATaskRunner`, `AgentA2ATaskRunner`, and `A2AServerAdapter`.

- [ ] **Step 4: Run green test**

Run:

```bash
uv run pytest tests/channels/test_a2a_server.py -q
```

Expected: all tests pass.

## Task 5: Minimal ASGI App

**Files:**
- Create: `src/agentos/channels/asgi.py`
- Test: `tests/channels/test_asgi_app.py`

- [ ] **Step 1: Write failing tests**

Use fake ASGI `scope`, `receive`, and `send` functions. Cover health route,
JSON turn route, SSE route, A2A task route, 404 route, auth failure returning
401, and `http.disconnect` causing `agent.interrupt()`.

- [ ] **Step 2: Run red test**

Run:

```bash
uv run pytest tests/channels/test_asgi_app.py -q
```

Expected: import failure for `agentos.channels.asgi`.

- [ ] **Step 3: Implement ASGI app**

Implement `AsgiAgentApp.__call__()` for HTTP scopes only. Use standard ASGI
`http.response.start` and `http.response.body` messages.

- [ ] **Step 4: Run green test**

Run:

```bash
uv run pytest tests/channels/test_asgi_app.py -q
```

Expected: all tests pass.

## Task 6: Remote Task Executor

**Files:**
- Create: `src/agentos/multi/remote.py`
- Test: `tests/multi/test_remote_dispatch.py`

- [ ] **Step 1: Write failing executor tests**

Cover successful `A2AAdapter.send_task()` callback and adapter exception mapping
to failed `TaskResult`.

- [ ] **Step 2: Run red test**

Run:

```bash
uv run pytest tests/multi/test_remote_dispatch.py -q
```

Expected: import failure for `agentos.multi.remote`.

- [ ] **Step 3: Implement RemoteTaskExecutor**

Implement `RemoteTaskExecutor` with `submit()` and `shutdown()`, backed by
`ThreadPoolExecutor`.

- [ ] **Step 4: Run green executor tests**

Run:

```bash
uv run pytest tests/multi/test_remote_dispatch.py -q
```

Expected: executor tests pass.

## Task 7: AgentCoordinator Remote Dispatch Branch

**Files:**
- Modify: `src/agentos/multi/coordinator.py`
- Modify: `src/agentos/multi/__init__.py`
- Test: `tests/multi/test_remote_dispatch.py`

- [ ] **Step 1: Write failing coordinator tests**

Add tests that endpoint-backed expert cards dispatch through
`RemoteTaskExecutor`, completed remote result is collectable by the parent,
remote failure becomes a failed task result, and remote late result does not
overwrite timeout/cancelled state.

- [ ] **Step 2: Run red test**

Run:

```bash
uv run pytest tests/multi/test_remote_dispatch.py -q
```

Expected: failure because `AgentCoordinator` has no remote executor path.

- [ ] **Step 3: Implement coordinator branch**

Add optional `remote_task_executor` to `AgentCoordinator.__init__()`. In
`dispatch()`, when the selected `AgentCard.endpoint` is present, create the task
record and submit to remote executor instead of sending a local inbox envelope.

- [ ] **Step 4: Run green coordinator tests**

Run:

```bash
uv run pytest tests/multi/test_remote_dispatch.py -q
```

Expected: all remote dispatch tests pass.

## Task 8: Public API And Drift Checks

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing API/drift tests**

Assert new channel public names are exported and `src/agentos/runtime` does not
contain `agentos.channels` imports.

- [ ] **Step 2: Run red test**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py -q
```

Expected: missing public exports.

- [ ] **Step 3: Add exports**

Export channel names from `agentos.channels`, root `agentos`, and
`agentos.multi` as appropriate.

- [ ] **Step 4: Run green API/drift tests**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py -q
```

Expected: all tests pass.

## Task 9: Verification

**Files:**
- All changed files.

- [ ] Run targeted channel and remote tests:

```bash
uv run pytest tests/channels tests/multi/test_remote_dispatch.py tests/architecture/test_public_api.py -q
```

- [ ] Run full suite:

```bash
uv run pytest -q
```

- [ ] Compile:

```bash
uv run python -m compileall -q src tests
```

- [ ] Whitespace check:

```bash
git diff --check
```

- [ ] Boundary drift search:

```bash
rg -n "agentos\\.channels" src/agentos/runtime src/agentos/context src/agentos/messages
```

Expected: no matches.
