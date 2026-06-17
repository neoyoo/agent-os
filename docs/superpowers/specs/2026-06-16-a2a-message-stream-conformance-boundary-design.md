# A2A Message Stream Conformance Boundary Design

> Date: 2026-06-16
> Phase: 60

## Target Conclusion

A2A self-conformance should track the SDK protocol surface that deployments are
told to rely on. After the `message/stream` server boundary, outbound client
initiation boundary, and typed SSE client event boundary exist, the SDK-owned
self-conformance harness should check those primitives too. It must remain a
local SDK evidence generator, not an official external conformance runner or
certification authority.

## Problem

`A2AConformanceHarness` already checks Agent Card shape, payload wrappers,
JSON-RPC envelopes, version headers, extension negotiation, and extension error
mapping. It does not yet verify that the SDK's own `message/stream` request and
SSE event payloads stay on the public A2A surface. This leaves readiness docs
claiming stream support while self-conformance evidence cannot show it.

## Scope

In scope:

- Add a self-conformance check for JSON-RPC `message/stream` request shape.
- Add a self-conformance check for typed SSE message stream event parsing.
- Keep checks inside `agentos.channels.a2a_conformance`.
- Reuse `A2AOperationRequest.message_stream(...)`,
  `a2a_operation_request_to_dict(...)`, and `parse_a2a_sse_events(...)`.
- Update readiness/docs/skill/roadmap to state that SDK self-conformance covers
  stream request and event primitives.

Out of scope:

- Running official or vendor-specific external conformance suites.
- Long-running SSE reconnect, fan-out, backpressure, durable cursor, gateway
  quota, billing, credential issuance, CA/DNS policy, or process supervision.
- Coupling `QueryLoop` or `AsyncQueryLoop` to A2A checks.

## API Shape

`A2AConformanceHarness.run(...)` continues to return `A2AConformanceReport` and
keeps existing keyword arguments. Add optional keyword arguments:

- `stream_operation_request_payload`
- `stream_event_payload`

The report adds two check ids:

- `message-stream-jsonrpc-envelope`
- `message-stream-event-shape`

## Acceptance

- Passing harness reports include both new check ids.
- A `message/send` operation supplied as `stream_operation_request_payload`
  fails `message-stream-jsonrpc-envelope`.
- A malformed stream event payload fails `message-stream-event-shape`.
- Readiness evidence and production docs mention stream self-conformance.
- Runtime boundary scan stays empty for A2A/planner/team terms in query loops.
