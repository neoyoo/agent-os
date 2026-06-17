# A2A Task Resubscribe Client Boundary Design

> Date: 2026-06-16
> Phase: 61

## Target Conclusion

A2A stream lifecycle should be composable from narrow SDK operations rather
than hidden long-running loops. The SDK should let outbound clients call
`tasks/resubscribe` through the same Agent Card URL, auth provider, protocol
version header, extension negotiation, trace propagation, and egress URL policy
used by `message/send` and `message/stream`. Automatic reconnect loops,
backpressure, fan-out, durable cursor storage, gateway quota, billing, and
credential issuance remain deployment or transport responsibilities.

## Problem

Server-side `tasks/resubscribe` already exists and ASGI uses it to follow task
updates. Outbound A2A clients can initiate `message/stream` and parse stream
events, but they do not have a first-class client method for one-shot cursor
resubscription after a stream disconnect. That forces applications to hand-roll
JSON-RPC payloads and can bypass the SDK's auth/version/extension/egress
boundaries.

## Scope

In scope:

- Add `A2AOperationClient.task_resubscribe(...)`.
- Use `A2AOperationRequest.task_resubscribe(...)` and
  `a2a_operation_response_from_dict(...)`.
- POST to `{card.url}/tasks/{task_id}:subscribe`.
- Include optional `after_event_id` as `afterEventId`.
- Reuse `_headers_for_card(...)` for auth, protocol version, caller headers,
  trace propagation, and extension negotiation.
- Reuse `_validate_url(...)` before transport execution.
- Update readiness/docs/skill/roadmap.

Out of scope:

- Long-running reconnect loops.
- Durable cursor persistence.
- SSE fan-out and backpressure control.
- Gateway/global quota, billing, credential issuance, CA/DNS policy, and
  process supervision.

## Acceptance

- Client posts JSON-RPC `tasks/resubscribe` to `/tasks/{id}:subscribe`.
- Response maps to `A2AOperationResponse.task_event`.
- Required unsupported card extensions fail before network I/O.
- Egress URL policy rejects blocked peers before network I/O.
- Readiness and docs distinguish one-shot cursor resubscribe from long-running
  stream lifecycle management.
