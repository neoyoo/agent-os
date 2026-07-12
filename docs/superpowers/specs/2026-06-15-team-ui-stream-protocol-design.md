# Team UI Stream Protocol Design (Phase 20A)

> Date: 2026-06-15
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Team runtime/tools/session/runner/daemon/retry/cancel/permission phases

## Target Conclusion

```text
Team discussion cannot be only backend state and daemon internals. The SDK
should expose a stable team UI event protocol with cursor/resume semantics so a
web UI can render team creation, membership changes, team messages, worker run
results, retries, cancellations, and deletion without coupling to TeamRuntime
or TeamWorkerRunner internals.
```

## Problem

The SDK now has team state, tools, distributed stores, worker sessions, runners,
daemon polling, retry/backoff, cancellation, and permission boundaries. A user
can build the backend flow, but a UI still has to infer what happened by reading
team messages, worker sessions, retry records, cancellation records, and daemon
state separately.

That is not a production-grade SDK surface. UI clients need one protocol stream
with stable event ids, event types, payloads, and replay-after-cursor behavior.

## Scope

Phase 20A adds:

- `TeamUiEventKind`
- `TeamUiEvent`
- `TeamUiStreamStore`
- `InMemoryTeamUiStreamStore`
- serializer helpers for UI events
- optional `ui_stream` injection into `TeamRuntime` and `TeamWorkerRunner`
- event publication for create/member/message/delete and worker run results
- public exports, readiness matrix, SDK skill guidance, and roadmap updates

Out of scope:

- ASGI/SSE endpoint for team UI events
- Redis/Postgres team UI stream store
- browser/frontend implementation
- replay compaction policy beyond bounded in-memory storage
- changing `QueryLoop` or `AsyncQueryLoop`

## Event Semantics

Event identity:

- `event_id` is a monotonically increasing integer per team stream.
- Cursor replay uses `after_event_id`.
- Events are ordered by `(event_id)`.

Event kinds:

- `team_created`
- `member_added`
- `message_appended`
- `worker_run_completed`
- `worker_run_failed`
- `worker_run_retry_skipped`
- `worker_run_cancelled`
- `team_deleted`

Payload rules:

- Payloads are JSON-safe dictionaries.
- Payloads contain stable ids and statuses, not local workspace roots.
- Team messages include message id, sender, recipient, kind, correlation id,
  artifact handles, metadata, and content.
- Worker run events include agent/session/delivery/message ids, status, retry
  fields, and cancellation status.

Store semantics:

- `append(team_id, kind, payload, created_at)` returns the stored event.
- `list_events(team_id, after_event_id=None)` returns events after a cursor.
- In-memory store is bounded per team.
- Store is protocol-shaped so a later Redis/Postgres implementation can provide
  distributed UI streaming without changing TeamRuntime/Runner contracts.

## Acceptance Criteria

- Creating a team appends `team_created`.
- Adding a member appends `member_added`.
- Saying a team message appends `message_appended`.
- Deleting a team appends `team_deleted`.
- Publishing worker runner results appends worker result events with stable
  kinds and payloads.
- UI events serialize/deserialize round trip.
- Cursor replay returns only events after the requested `event_id`.
- Public API exports UI event/store types.
- Readiness/docs mark team UI stream protocol as SDK primitive while keeping
  network/SSE endpoint and distributed UI stream storage as future/app-owned
  work.
- Runtime loop files do not import team UI stream types.
