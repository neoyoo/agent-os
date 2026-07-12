# A2A Task Lifecycle Design

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Phase: 11B

## Target Conclusion

```text
message/send starts an A2A interaction, but production interoperability also
needs task lookup and cancel operations. agent-os should project internal
TaskStore state into A2A task lifecycle semantics through a protocol boundary,
not by exposing the internal /a2a/tasks bridge as full A2A.
```

## Current State

Phase 11A added:

- `A2AMessage`
- `A2ATask`
- `A2AOperationRequest` / `A2AOperationResponse`
- `A2AOperationServer` / `A2AOperationClient`
- ASGI `POST /a2a/message:send`

The current external A2A boundary still cannot:

- retrieve a task by id
- cancel a queued/running task
- distinguish task-not-found from not-cancelable
- project internal agent-os `TaskRecord` statuses into A2A task states

Existing internal support:

- `TaskStore.get(task_id)`
- `TaskStore.request_cancel(task_id, now=...)`
- `AgentCoordinator.cancel(task_id)`
- task terminal statuses and cancel intent on `TaskRecord`

## External Baseline

A2A v1.0 exposes task lifecycle operations with REST routes equivalent to:

- `GET /tasks/{id}`
- `POST /tasks/{id}:cancel`

Phase 11B implements these routes under the configured A2A base path:

- `GET /a2a/tasks/{task_id}`
- `POST /a2a/tasks/{task_id}:cancel`

This does not implement streaming task updates, push notification config, or
task resubscription.

## Architecture

Extend `agentos.channels.a2a_operations` with task lifecycle collaborators:

- `a2a_task_from_task_record(record)`: maps internal `TaskRecord` to `A2ATask`.
- `A2ATaskLifecycleRunner`: protocol with `get_task(task_id)` and
  `cancel_task(task_id)`.
- `TaskStoreA2ATaskLifecycleRunner`: adapter around `TaskStore`.
- `A2AOperationServer.handle_task_get(task_id)` and `handle_task_cancel(task_id)`.
- `A2AOperationClient.get_task(card, task_id)` and `cancel_task(card, task_id)`.

`A2AOperationServer` keeps the operation boundary separate from
`A2AServerAdapter`:

- `/a2a/tasks` remains internal agent-os task bridge.
- `/a2a/tasks/{id}` and `/a2a/tasks/{id}:cancel` are protocol lifecycle routes.

## State Mapping

Internal `TaskStatus` -> A2A `A2ATaskState`:

- `queued` -> `submitted`
- `running` -> `working`
- `completed` -> `completed`
- `failed` -> `failed`
- `cancelled` -> `canceled`
- `timeout` -> `failed`

If a running task has `cancel_requested_at`, the A2A task remains `working` and
includes metadata `cancelRequested: true`.

Task result projection:

- terminal result summary becomes an agent message.
- result artifacts become A2A artifacts.
- record metadata includes parent/target agent ids, internal status, worker id,
  attempt, created/deadline/completed timestamps, and cancel request flag.

## Error Handling

Use JSON-RPC-like operation errors consistently:

- task not found: `code=-32001`, message `task not found`
- not cancelable: `code=-32002`, message `task not cancelable`
- invalid task id: `code=-32602`

ASGI still returns HTTP 200 for protocol error envelopes when the operation
server is configured; 404 is reserved for missing operation server/routes.

## Readiness Position

After Phase 11B:

- A2A has first-class `message/send`, task lookup, and task cancel boundaries.
- It remains `primitives-ready` because streaming, push notifications,
  resubscription, auth/trust, signed cards, and external conformance remain
  incomplete.

## Verification

- Unit tests for internal status -> A2A state mapping.
- Unit tests for task get/cancel success and error paths.
- Client tests for task lifecycle URLs and response parsing.
- ASGI route tests for `GET /a2a/tasks/{id}` and
  `POST /a2a/tasks/{id}:cancel`.
- Public API and readiness/docs tests.
- Full runtime boundary search to keep `QueryLoop` / `AsyncQueryLoop`
  deployment-agnostic.
