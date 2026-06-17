# A2A Task Resubscribe Boundary Design

## Target Conclusion

Task subscription is a protocol operation, not just an ASGI route. Public A2A
services should expose task resubscribe through the same operation boundary used
for message, task, push config, auth, extension negotiation, protocol version
checks, and per-peer rate limiting. The SDK should own a narrow
`tasks/resubscribe` operation primitive that returns the next task subscription
event for a task cursor. Long-lived SSE connection management, backpressure,
fan-out, and gateway quota policy remain transport/deployment-owned.

## Context

The current SDK already has `A2ATaskLifecycleRunner.next_task_update(...)` and
an ASGI `POST /a2a/tasks/{id}:subscribe` SSE loop. That SSE loop reads directly
from the lifecycle runner, so it can bypass the newer operation-layer boundaries:
protocol version negotiation, extension negotiation, inbound peer auth,
resource-aware authorization, and per-peer rate limiting. That is not good enough
for a public A2A surface.

A2A 1.0 keeps the protocol model layered: abstract operations map to concrete
bindings, and Subscribe to Task maps to `POST /tasks/{id}:subscribe`. Streaming
responses are ordered task/update events. The SDK should preserve that split:
`A2AOperationServer` owns the operation decision and one-event projection;
ASGI owns the SSE transport loop.

References:

- https://github.com/a2aproject/A2A/blob/main/docs/specification.md

## Scope

In scope:

- Add `A2AOperationRequest.task_resubscribe(...)`.
- Let `A2AOperationResponse` carry an optional `A2ATaskSubscriptionEvent`.
- Add `A2AOperationServer.handle_task_resubscribe(...)`.
- Support JSON-RPC `tasks/resubscribe` in `handle_operation(...)`.
- Route ASGI task subscribe through the server boundary before streaming.
- Preserve the current ASGI SSE event shape and cursor behavior.
- Update readiness docs and the agent-os skill guidance.

Out of scope:

- Full `message/stream` operation implementation.
- Distributed SSE fan-out or persistent stream cursor stores.
- Gateway/global quota stores, billing policy, or commercial entitlements.
- External conformance suite execution.

## API Shape

`A2AOperationRequest.task_resubscribe(task_id, after_event_id=None, request_id=None)`
creates:

```json
{
  "jsonrpc": "2.0",
  "id": "req_1",
  "method": "tasks/resubscribe",
  "params": {
    "id": "task_1",
    "afterEventId": 4
  }
}
```

`A2AOperationServer.handle_task_resubscribe(...)` returns a JSON-RPC response.
When a new event is available, `result` is the existing A2A 1.0 `statusUpdate`
event payload. When the task exists but has no newer event for the cursor, the
result is empty. Missing tasks still return the existing task-not-found error.

## Boundary Rules

Every task resubscribe call must run in this order:

1. Protocol version check.
2. Extension negotiation.
3. Inbound peer/resource authorization with operation `tasks/resubscribe`.
4. Per-peer rate limit with operation `tasks/resubscribe` and task resource.
5. Task lifecycle lookup/update projection.

Unknown JSON-RPC methods remain unsupported and must not consume rate-limit
buckets.

## Testing

Use TDD:

- RED operation tests for JSON-RPC request serialization, response event
  round-trip, auth/resource context, and rate-limit keys.
- RED ASGI tests proving `/a2a/tasks/{id}:subscribe` rejects unauthorized peers
  and rate-limited peers before SSE starts.
- GREEN implementation in `agentos.channels.a2a_operations` and
  `agentos.channels.asgi`.
- Public API, readiness, docs, and skill tests.
- Final verification with targeted tests, full pytest, compileall, runtime
  boundary scan, and `git diff --check`.
