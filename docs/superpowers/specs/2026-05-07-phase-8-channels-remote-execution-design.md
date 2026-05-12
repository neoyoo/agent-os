# Phase 8 Channels + Remote Execution Design

## Scope Contract

This design belongs to Phase 8 `channel-remote` and remote agent execution.

It completes the first production SDK slice for inbound channel access and
remote agent dispatch:

- HTTP JSON channel for one complete agent turn.
- HTTP SSE channel for existing typed stream events.
- Minimal ASGI app without FastAPI or Starlette dependency.
- Inbound A2A task and health adapter.
- Session provider boundary with in-memory session caching.
- Remote `AgentCard.endpoint` dispatch through outbound `A2AAdapter`.
- Remote task results remain available only through `check_agent_tasks`.

It intentionally defers:

- Eval runner and metrics.
- Finetuning exporter and prompt optimization.
- WebSocket and realtime duplex channels.
- OAuth, API key rotation, rate limiting, and multi-tenant auth.
- Production sandbox isolation beyond existing tool policy boundaries.
- Real network integration tests.
- Persistent session hydration provider.

The design must not make `QueryLoop`, `ContextRuntime`, or `MessageRuntime`
import or depend on `agentos.channels`.

## Architecture

`channels/` is the SDK entry adapter layer. It converts external request shapes
into calls against the public `Agent` facade and converts typed stream events
back into JSON or SSE. It must not reach into `QueryLoop` internals.

The runtime shape is:

```text
HTTP/SSE/A2A request
  -> ChannelAuthPolicy
  -> AgentSessionProvider
  -> Agent.run / Agent.stream
  -> QueryLoop
```

Remote dispatch shape is:

```text
AgentCoordinator.dispatch()
  -> registry selects AgentCard
  -> endpoint absent: existing local inbox path
  -> endpoint present: RemoteTaskExecutor -> A2AAdapter.send_task()
  -> TaskTable terminal result
  -> ContinuationTrigger notice
  -> parent reads via check_agent_tasks
```

## Public Channel Modules

Add:

```text
src/agentos/channels/types.py
src/agentos/channels/auth.py
src/agentos/channels/session.py
src/agentos/channels/http.py
src/agentos/channels/sse.py
src/agentos/channels/asgi.py
src/agentos/channels/a2a_server.py
```

Keep:

```text
src/agentos/channels/a2a.py
```

`a2a.py` remains the outbound A2A client adapter. `a2a_server.py` handles
inbound A2A payloads but does not implement HTTP routing by itself.

## Session Provider

Define:

```python
class AgentSessionProvider(Protocol):
    def get_agent(self, session_id: str) -> Agent: ...
    def release_agent(self, session_id: str, agent: Agent) -> None: ...
```

`release_agent()` means "this channel call finished." The default implementation
does not destroy the agent. Session closing, TTL eviction, and persistent
hydration are later extensions.

`InMemoryAgentSessionProvider` accepts `agent_factory: Callable[[str], Agent]`.
Unknown sessions are created on first turn using the caller-provided
`session_id`.

## HTTP JSON Channel

Route:

```text
POST /v1/sessions/{session_id}/turns
```

Input:

```json
{
  "message": "hello",
  "thinking": false,
  "show_thinking": false
}
```

Output:

```json
{
  "session_id": "session_1",
  "status": "completed",
  "content": "..."
}
```

Errors:

- Invalid JSON returns 400.
- Missing or empty `message` returns 400.
- Agent exception returns 500.
- The response body is JSON and contains `status="failed"` plus `error`.

## SSE Channel

Route:

```text
POST /v1/sessions/{session_id}/turns/stream
```

The request body matches the HTTP JSON channel. The output must reuse
`event_to_sse()` from `agentos.runtime.stream_serializers`; the channel must not
create a second stream event schema.

If ASGI receives `http.disconnect` while streaming, the app calls
`agent.interrupt()` best-effort. This does not promise forceful cancellation of
provider or tool calls; it only enters the existing interrupt safe-point path.

## ASGI App

`AsgiAgentApp` is included in the first version. It is a minimal ASGI callable
responsible for:

- Routing request method and path.
- Reading the request body.
- Calling `ChannelAuthPolicy`.
- Delegating to `HttpAgentChannel`, `SseAgentChannel`, and `A2AServerAdapter`.
- Writing ASGI HTTP responses.
- Returning 404 for unknown paths.

It must not import FastAPI, Starlette, uvicorn, or httpx.

Supported routes:

```text
GET  /v1/health
POST /v1/sessions/{session_id}/turns
POST /v1/sessions/{session_id}/turns/stream
POST /a2a/tasks
GET  /a2a/health
```

There is no `POST /sessions/create`. First `POST /turns` creates the session.

## Auth Boundary

Define:

```python
class ChannelAuthPolicy(Protocol):
    def authorize(self, headers: Mapping[str, str]) -> None: ...
```

Default:

```text
AllowAllChannelAuthPolicy
```

`AllowAllChannelAuthPolicy` is explicitly local/dev only. Production users must
place auth in a gateway or pass a custom `ChannelAuthPolicy`. The SDK does not
ship a static bearer token implementation in this slice because it would imply
security properties the SDK cannot provide without rotation, storage, and
deployment policy.

## A2A Server

Define:

```python
class A2ATaskRunner(Protocol):
    def run_task(self, request: TaskRequest) -> TaskResult: ...

class A2AServerAdapter:
    def handle_task(self, payload: dict[str, object]) -> dict[str, object]: ...
    def handle_health(self) -> dict[str, object]: ...
```

Default runner:

```text
AgentA2ATaskRunner
  -> Agent.run(request.instruction)
  -> TaskResult(status="completed")
```

`allowed_tool_names` is parsed and retained in `TaskRequest`. The default runner
does not enforce it. Tool restriction remains in `ToolCallRouter` and policy
layers. A later `RestrictedA2ATaskRunner` can enforce per-task tool policy
without changing transport code.

## Remote Coordinator Bridge

Add `RemoteTaskExecutor` in `agentos.multi.remote`.

It exposes:

```python
def submit(
    self,
    card: AgentCard,
    request: TaskRequest,
    on_result: Callable[[TaskResult], None],
) -> None: ...
```

The executor runs outbound `A2AAdapter.send_task()` in a background executor and
calls `on_result` with either the remote result or a failed `TaskResult`.

Extend `AgentCoordinator.dispatch()` directly:

- Select a card the same way as local dispatch.
- If `card.endpoint is None`, keep the current inbox dispatch path.
- If `card.endpoint is not None`, create a task record and submit it through
  `RemoteTaskExecutor`.

Remote completion rules:

- Completed remote task marks `TaskTable` completed.
- Network or adapter exception marks `TaskTable` failed.
- If the task is already terminal because of timeout or cancellation, the
  remote result is stored as `late_result` and does not overwrite the terminal
  state.
- Completion sends parent result envelope best-effort and triggers
  `ContinuationTrigger`.
- Parent LLM still consumes results through `check_agent_tasks`; no remote
  result is appended to `MessageStore`.

## Testing

Use deterministic fakes. Do not start a real server and do not require network
dependencies.

Targeted test files:

```text
tests/channels/test_http_channel.py
tests/channels/test_sse_channel.py
tests/channels/test_asgi_app.py
tests/channels/test_a2a_server.py
tests/multi/test_remote_dispatch.py
tests/architecture/test_public_api.py
```

Required coverage:

- Unknown session is created on first turn.
- `release_agent()` is called after each channel turn.
- JSON channel returns completed content.
- Invalid JSON and missing message return 400.
- SSE channel reuses typed stream event serializer output.
- ASGI health route returns JSON.
- ASGI routes JSON turn, SSE turn, and A2A task.
- ASGI disconnect calls `agent.interrupt()` best-effort.
- A2A server task payload produces TaskResult JSON.
- Remote dispatch with endpoint card uses outbound A2A path.
- Remote failure becomes failed task result.
- Remote result does not enter `MessageStore`; parent reads it through
  `collect_results()` / `check_agent_tasks`.
- Drift search confirms `src/agentos/runtime` does not import
  `agentos.channels`.

## Completion Checklist

- Channel API implemented in `agentos.channels`.
- Remote dispatch bridge implemented in `agentos.multi`.
- Public exports added to `agentos.channels` and root `agentos`.
- Targeted tests pass.
- Full test suite passes.
- `python -m compileall -q src tests` passes.
- `git diff --check` passes.
- Drift search confirms runtime-to-channel boundary is clean.
