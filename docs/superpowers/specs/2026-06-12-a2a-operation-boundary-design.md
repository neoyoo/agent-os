# A2A Operation Boundary Design

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Phase: 11A

## Target Conclusion

```text
A2A Agent Card and discovery are only the interoperability entry point.
agent-os needs a protocol operation boundary that separates external A2A
message/task semantics from the internal AgentCoordinator task bridge.
```

## Current State

agent-os already has:

- `A2AAgentCard`, `A2AAgentSkill`, `A2ACardResolver`
- `/.well-known/agent-card.json` and `/a2a/agent-card`
- internal `/a2a/tasks` through `A2AAdapter` and `A2AServerAdapter`
- internal `TaskRequest` / `TaskResult` for agent-os multi-agent dispatch

The missing piece is an A2A-facing operation boundary. Current `/a2a/tasks`
must remain documented as an internal JSON bridge, not protocol parity.

## External Baseline

The A2A protocol surface is centered on an Agent Card plus message/task
operations such as `message/send`, streaming message send, task lookup,
cancelation, resubscription, and push notification configuration. AgentScope2's
service/team model reinforces the same boundary: service hosting owns session,
storage, message bus, workspace, scheduling, and credentials rather than making
workers share active runtime state.

Phase 11A implements only the minimal operation boundary:

- message send request/response models
- task state mapping
- JSON-RPC-like payload helpers
- an inbound operation server for `message/send`
- an outbound operation client for `message/send`
- ASGI route exposure separate from `/a2a/tasks`

Deferred:

- `message/stream`
- task lookup/cancel/resubscribe routes
- push notification configuration
- signed cards and trust store
- per-peer auth enforcement
- full protocol conformance tests against external servers

## Architecture

Add `agentos.channels.a2a_operations` as a separate protocol surface:

- `A2AMessagePart`: typed message content part.
- `A2AMessage`: role, parts, message id, context id, task id, metadata.
- `A2ATaskState`: protocol-facing task state enum.
- `A2ATask`: task id, context id, status, artifacts, metadata.
- `A2AOperationRequest` / `A2AOperationResponse`: JSON-RPC-like envelope.
- `A2AOperationRunner`: protocol for app-owned execution of message operations.
- `AgentA2AOperationRunner`: adapts an `Agent` to `message/send`.
- `A2AOperationServer`: parses operation payloads and invokes the runner.
- `A2AOperationClient`: sends operation payloads to an `A2AAgentCard` endpoint.

`A2AAdapter` and `A2AServerAdapter` stay as the internal task bridge. They are
not renamed or promoted to full A2A protocol compliance.

## Data Flow

Inbound protocol request:

```text
POST /a2a/message:send
  -> AsgiAgentApp
  -> A2AOperationServer.handle_message_send(payload, headers)
  -> A2AOperationRunner.send_message(A2AMessage)
  -> AgentA2AOperationRunner calls Agent.run(text)
  -> A2AOperationResponse containing A2ATask + assistant A2AMessage
```

Outbound protocol request:

```text
A2AOperationClient.send_message(card, message)
  -> transport.post_json(card.url + "/message:send", payload)
  -> A2AOperationResponse.from_dict(...)
```

## Error Handling

- Invalid operation payloads return an operation response with an `error`
  object; they do not raise through ASGI.
- Unsupported methods return `code=-32601`.
- Invalid params return `code=-32602`.
- Runner exceptions return a failed task state and an error object.
- Trace headers are passed through the same incoming/outgoing header boundary
  as the existing internal task bridge.

## Readiness Position

After Phase 11A, A2A remains `primitives-ready`, not production-ready:

- `message/send` has a first-class boundary.
- streaming, push notification, task lifecycle routes, auth enforcement, trust,
  and external conformance remain gaps.
- SDK docs must say "A2A operation boundary started", not "full A2A compliant".

## Verification

- Unit tests for message/task/envelope serialization.
- Unit tests for operation client URL/payload/response behavior.
- Unit tests for operation server success, invalid payload, and unsupported
  method.
- ASGI route test for `/a2a/message:send`.
- Public API tests for new operation types.
- Readiness/docs tests continue to block full A2A claims.
- Boundary search confirms `QueryLoop` and `AsyncQueryLoop` still do not import
  A2A/channel protocol details.
