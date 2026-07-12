# A2A Message Stream Client Events Design

> Date: 2026-06-16  
> Branch: `review/agentos-sdk-architecture-20260611`  
> Phase: 59

## Target Conclusion

An A2A SDK client should not stop at initiating `message/stream`; it should be
able to consume the peer's `text/event-stream` response as typed protocol
events. The SDK should own a narrow SSE parsing and client-consumption boundary
that preserves the same Agent Card URL, auth provider, protocol version,
extension negotiation, and egress policy controls as `stream_message(...)`.
Reconnect loops, durable stream cursors, fan-out, backpressure policy, gateway
quota, and billing remain transport or deployment responsibilities.

## Current State

Phase 57 added inbound `message/stream` handling and ASGI SSE output. Phase 58
added `A2AOperationClient.stream_message(...)`, but that helper only parses the
initial JSON operation response. The base `A2ATransport` protocol still exposes
JSON request methods only, so SDK users who need actual A2A streaming events
must parse SSE frames manually and may bypass outbound egress/auth/version/
extension controls.

The existing model already has typed projections for most stream payloads:

- `A2ATask`
- `A2AMessage`
- `A2ATaskSubscriptionEvent`
- `A2ATaskArtifactUpdateEvent`

## Design

Add a narrow stream event model:

- `A2AMessageStreamEvent`
  - `event`: original SSE event name when present
  - `task`: populated for initial task events
  - `message`: populated for message events
  - `task_event`: populated for `statusUpdate` events
  - `artifact_event`: populated for `artifactUpdate` events
  - `raw`: original decoded JSON payload

Add parser helpers:

- `parse_a2a_sse_events(chunks)` parses text or bytes chunks into SSE events.
- `a2a_message_stream_event_from_dict(payload, event=None)` converts A2A
  StreamResponse payloads into typed SDK values.

Add transport/client boundary:

- Extend `A2ATransport` with `post_sse(...)` returning an iterable of text or
  bytes chunks.
- Implement `UrllibA2ATransport.post_sse(...)` with `Accept:
  text/event-stream` and `Content-Type: application/json`.
- Add `A2AOperationClient.stream_message_events(...)` that builds the same
  `message/stream` JSON-RPC payload as `stream_message(...)`, validates egress,
  merges headers, calls `post_sse(...)`, and yields parsed
  `A2AMessageStreamEvent` values.

## Scope Boundaries

The SDK will not own in this phase:

- reconnect loops
- Last-Event-ID persistence
- durable stream cursor storage
- local fan-out to multiple consumers
- backpressure queues
- gateway/global quota
- billing/entitlement policy

Those responsibilities require deployment-specific storage and network
topology decisions.

## Security Boundary

The new client consumption path must not bypass existing outbound controls:

- egress URL policy must run before transport
- auth provider headers must be merged through `_headers_for_card(...)`
- protocol version headers must be sent by default and remain caller-overridable
- extension negotiation must reject required unsupported extensions before any
  network call
- parser errors must not include bearer tokens or raw headers

## Testing

Add focused tests that prove:

- parser handles multi-line SSE `data:` frames and comments
- parser maps `task`, `message`, `statusUpdate`, and `artifactUpdate` payloads
  into typed SDK events
- client calls `/message:stream` through `post_sse(...)`, with protocol,
  auth/extension headers, timeout, and JSON-RPC payload
- required unsupported extensions fail before network calls
- egress policy rejects blocked stream calls before transport

## Production Readiness

This phase makes A2A streaming consumable through SDK primitives, but the A2A
form remains primitives-ready because production deployments still own stream
resumption strategy, cursor persistence, load-balanced fan-out, backpressure,
gateway quotas, credential issuance, CA rollout, DNS/egress governance,
supervised worker processes, and external conformance suite execution.
