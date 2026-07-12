# A2A Message Stream Operation Boundary Design

## Target Conclusion

A2A streaming should not be an ad hoc transport feature. The SDK should expose a narrow `message/stream` operation boundary that starts work through the same protocol version, extension negotiation, inbound peer authorization, resource authorization, trace propagation, and per-peer rate-limit controls as `message/send`, then returns an initial stream projection suitable for ASGI SSE transport. Long-running stream fan-out, backpressure, durable stream cursor storage, and gateway/global quota enforcement remain deployment-owned.

## Context

The current SDK owns `message/send`, task get/cancel, `tasks/resubscribe`, task subscribe/SSE status updates, push notification configuration, peer auth, extension negotiation, protocol version checks, and local per-peer operation rate limiting. `tasks/resubscribe` now lets ASGI subscribe reuse the operation boundary before starting SSE.

The remaining A2A streaming gap is the initiating operation: a peer can call `message/send`, then subscribe to a task, but cannot call a protocol-facing `message/stream` boundary that initiates work and opens the initial stream. This keeps the A2A form short of core operation parity for streaming web agents.

## Scope

This phase owns:

- `A2AOperationRequest.message_stream(...)`.
- JSON-RPC `message/stream` dispatch in `A2AOperationServer.handle_operation(...)`.
- `A2AOperationServer.handle_message_stream(...)` as the explicit operation primitive.
- Reuse of version checks, extension negotiation, inbound auth, trace propagation, and operation rate limiting.
- Initial response serialization using the existing `A2AOperationResponse(task=...)` projection.
- ASGI `POST /a2a/message:stream` endpoint that performs the operation boundary before starting SSE.
- ASGI SSE follow-up through the existing `handle_task_resubscribe(...)` task event boundary when the initial response contains a task.

This phase does not own:

- Multi-subscriber broadcast fan-out.
- Stream backpressure strategy.
- Durable SSE event cursor storage.
- Distributed/global quota storage.
- Gateway enforcement, commercial billing tiers, or entitlement policy.
- A separate streaming execution engine inside `QueryLoop` or `AsyncQueryLoop`.

## Protocol Shape

`message/stream` uses the same request params as `message/send`:

```json
{
  "jsonrpc": "2.0",
  "id": "req_stream",
  "method": "message/stream",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"text": "hello"}]
    }
  }
}
```

The operation server returns an initial `A2AOperationResponse` with `result.task`.
The ASGI streaming endpoint writes that initial response as the first SSE event. If the task lifecycle runner is available, ASGI then polls `tasks/resubscribe` for status updates until the task reaches a final state or the configured idle timeout expires.

## Security And Boundary Order

For `message/stream`, the SDK-owned order is:

1. Validate protocol version.
2. Negotiate extensions.
3. Authorize inbound peer for operation `message/stream`.
4. Apply local per-peer operation rate limit using operation `message/stream`.
5. Parse `params.message`.
6. Execute the configured A2A operation runner under incoming trace headers.
7. Return the initial task projection.

Unsupported JSON-RPC methods must not consume rate-limit buckets. ASGI must not open SSE until the operation boundary succeeds.

## Acceptance Criteria

- `message/stream` request/response round-trips through `A2AOperationRequest` and the existing response serializers.
- `A2AOperationServer.handle_operation(...)` handles JSON-RPC `message/stream` instead of returning unsupported method.
- `message/stream` uses operation-specific auth and rate-limit keys.
- Unsupported methods still return `-32601` without runner/store/rate-limit work.
- ASGI `POST /a2a/message:stream` returns JSON operation errors before SSE starts.
- ASGI `POST /a2a/message:stream` emits an initial task SSE event and then reuses `tasks/resubscribe` for subsequent task events.
- Runtime boundary scan confirms `src/agentos/runtime/query_loop.py` and `src/agentos/runtime/async_query_loop.py` remain free of A2A/planner/channel/team concepts.
