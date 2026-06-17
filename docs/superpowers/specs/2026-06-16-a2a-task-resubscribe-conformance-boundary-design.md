# A2A Task Resubscribe Conformance Boundary Design

> Date: 2026-06-16
> Phase: 62

## Target Conclusion

A2A self-conformance should track the SDK protocol surface that deployments are
told to rely on. After `tasks/resubscribe` exists on the server and outbound
client boundary, the SDK-owned self-conformance harness should verify the
JSON-RPC request envelope and the `statusUpdate` task subscription event shape.
External conformance suite execution, automatic reconnect loops, durable cursor
storage, fan-out, backpressure, gateway quota, billing, and credential issuance
remain deployment/profile work.

## Problem

The SDK now exposes `A2AOperationRequest.task_resubscribe(...)`,
`A2AOperationServer.handle_task_resubscribe(...)`, the ASGI
`POST /a2a/tasks/{id}:subscribe` route, and
`A2AOperationClient.task_resubscribe(...)`. The self-conformance harness checks
generic operation envelopes and `message/stream`, but it does not explicitly
check the `tasks/resubscribe` method or the `statusUpdate` event shape that
clients receive after cursor resubscription. That leaves a gap between the
production-readiness claim and the SDK's local compatibility evidence.

## Scope

In scope:

- Add a `task-resubscribe-jsonrpc-envelope` self-conformance check.
- Add a `task-resubscribe-event-shape` self-conformance check.
- Use default sample payloads built from `A2AOperationRequest.task_resubscribe`
  and `a2a_task_subscription_event_to_dict`.
- Allow callers to pass custom task resubscribe request and event payloads to
  `A2AConformanceHarness.run(...)`.
- Fail non-`tasks/resubscribe` request payloads.
- Fail malformed or non-`statusUpdate` event payloads.
- Update readiness, production docs, agent-os skill guidance, and roadmap.

Out of scope:

- Running external or official A2A conformance suites.
- Network calls to peer agents.
- Reconnect loops, durable cursor persistence, SSE fan-out, backpressure,
  quotas, billing, credential issuance, process supervision, or gateway policy.

## Acceptance

- Default `A2AConformanceHarness().run(card)` includes passing
  `task-resubscribe-jsonrpc-envelope` and `task-resubscribe-event-shape`
  findings.
- Passing a non-resubscribe operation request to the task-resubscribe check
  fails with a clear detail mentioning `tasks/resubscribe`.
- Passing a malformed event payload without `statusUpdate` fails with a clear
  detail mentioning `statusUpdate`.
- Readiness and docs explicitly name the tasks/resubscribe self-conformance
  checks and keep external suite execution and stream lifecycle management as
  deployment-owned work.
