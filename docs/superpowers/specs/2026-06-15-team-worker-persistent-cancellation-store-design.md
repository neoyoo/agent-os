# Team Worker Persistent Cancellation Store Design (Phase 19A)

> Date: 2026-06-15
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 17A cancellation intent primitives and Phase 18A worker permission boundary

## Target Conclusion

```text
Team worker cancellation intent cannot be process-local in a production
cluster. The SDK should provide a durable TeamWorkerCancellationStore adapter
so any worker node can observe, acknowledge, and clear cancellation requests
before executing queued or retry-delayed continuations.
```

## Problem

`TeamWorkerRunner` and `TeamWorkerDaemon` already honor
`TeamWorkerCancellationStore`, but the SDK only ships an in-memory store. In a
multi-node team deployment, cancellation intent can be lost when a worker
restarts or invisible when the next delivery is processed by a different node.

The retry boundary already has a Postgres adapter. Cancellation needs the same
production-shaped durability behind the existing protocol.

## Scope

Phase 19A adds:

- `PostgresTeamWorkerCancellationStore`
- migration `2026-06-15-postgres-team-worker-cancellations.sql`
- tests for request, match, acknowledge, clear, list, ordering, and migration
- public exports, readiness matrix, SDK skill guidance, and roadmap updates

Out of scope:

- changing runner cancellation semantics
- interrupting already-running continuations
- A2A cancellation propagation
- UI stream protocol
- tool sandbox enforcement

## Store Semantics

The Postgres adapter should match the in-memory store contract:

- `request_cancel(record)` upserts the exact cancellation intent.
- `match(...)` returns the best active requested record:
  - exact `delivery_id` or `message_id` match wins before worker-scope records
  - older matching records win within the same specificity
  - acknowledged and cleared records are not active matches
- `acknowledge(record, now=...)` marks the same intent acknowledged.
- `clear(record, now=...)` marks the same intent cleared.
- `list_records(team_id=None)` returns durable records in deterministic order.

Identity:

- key by `agent_id`, `session_id`, `delivery_id`, and `message_id`
- store nullable delivery/message identity in payload and indexed columns
- use sentinel-safe SQL behavior for nullable key fields

## Acceptance Criteria

- Postgres cancellation store round-trips worker-scope and delivery/message
  scoped records.
- Match behavior mirrors `InMemoryTeamWorkerCancellationStore`.
- Acknowledge and clear update status/timestamps and stop future matches.
- Records can be listed by team or across teams in deterministic order.
- Migration has up/down paths, table, primary key or unique index, and useful
  active-match indexes.
- Public API exports the Postgres store.
- Readiness/docs move persistent cancellation storage from app-owned gap to SDK
  primitive while keeping UI stream and tool sandbox enforcement as gaps.
- Runtime loop files do not import team cancellation persistence types.
