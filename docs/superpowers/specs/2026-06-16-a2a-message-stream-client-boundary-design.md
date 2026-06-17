# A2A Message Stream Client Boundary Design

> Date: 2026-06-16  
> Branch: `review/agentos-sdk-architecture-20260611`  
> Phase: 58

## Target Conclusion

Outbound A2A clients should be able to initiate `message/stream` through the
same Agent Card URL, auth provider, protocol version header, extension
negotiation, and egress URL policy as `message/send`. The SDK should own the
initial operation request and response boundary. Long-lived SSE consumption,
retry, backpressure, durable cursors, stream fan-out, and gateway/global quota
remain transport or deployment responsibilities.

## Current State

Phase 57 added the inbound `message/stream` operation boundary:

- `A2AOperationRequest.message_stream(...)`
- `A2AOperationServer.handle_message_stream(...)`
- JSON-RPC dispatch for `message/stream`
- ASGI `POST /a2a/message:stream` that emits initial task SSE and follows
  task updates through `tasks/resubscribe`

The outbound client still exposes `send_message(...)`, task operations, and push
notification config helpers, but it has no direct `stream_message(...)` helper.
SDK users would have to manually assemble the JSON-RPC payload and endpoint,
which risks bypassing protocol version headers, auth headers, extension
negotiation, and egress URL policy.

## Design

Add `A2AOperationClient.stream_message(...)` next to `send_message(...)`.

The method will:

- accept the same `card`, `message`, `request_id`, `timeout_seconds`, and
  `headers` shape as `send_message(...)`
- build `A2AOperationRequest.message_stream(...)`
- post JSON to `card.url.rstrip("/") + "/message:stream"`
- call the same `_validate_url(...)` egress policy check before transport
- call the same `_headers_for_card(...)` helper for protocol version, auth, and
  extension negotiation
- parse the transport's first JSON response as `A2AOperationResponse`

The method will not:

- parse server-sent event streams
- own stream reconnect, backpressure, fan-out, or cursor persistence
- introduce async stream readers
- add A2A or streaming concepts to `QueryLoop` or `AsyncQueryLoop`

## Security Boundary

The new method must not introduce a path around existing client controls:

- egress URL policy is checked before any transport call
- auth provider headers are included exactly as for `message/send`
- caller-provided headers may override protocol version as they already can for
  `send_message(...)`
- required remote extensions are rejected before transport by the existing
  extension negotiation policy

## Testing

Add focused tests that prove:

- `stream_message(...)` posts to `/message:stream` with method
  `message/stream`
- request id, message payload, timeout, protocol version header, and
  caller-provided trace header are preserved
- supported extension headers are negotiated for `stream_message(...)`
- required unsupported extensions fail before network calls
- egress URL policy rejects blocked peers before transport calls

## Production Readiness

This phase raises A2A from server-only streaming initiation to symmetric
operation initiation support. The A2A form remains primitives-ready, not
full-production-complete, because deployment still owns:

- actual SSE client consumption and retries
- durable stream cursor storage
- stream fan-out and backpressure
- gateway or global quota stores
- credential issuance and rotation operations
- CA rollout, DNS pinning, and enterprise egress proxy policy
- external conformance suite execution
