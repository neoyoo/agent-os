# Team Worker Persistent Retry Store Design (Phase 16B)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 16A team worker retry boundary

## Target Conclusion

```text
In-memory retry/backoff prevents local hot loops, but distributed team workers
need retry state that survives process restarts and can be shared by multiple
nodes. The SDK should ship a Postgres TeamWorkerRetryStore adapter behind the
Phase 16A retry protocol, while leaving claim/lease scheduling and cancellation
as later phases.
```

## Problem

`InMemoryTeamWorkerRetryStore` is suitable for tests and single-node workers,
but it cannot support the target distributed team shape:

- a worker process can restart and lose retry state
- another node may need to continue a due retry
- daemon state cannot be inspected centrally
- retry store migration is not part of deployment guidance

The SDK already ships `PostgresTeamStore` for shared team records/messages. A
matching Postgres retry store should persist failed worker deliveries and retry
metadata without changing the `AgentMessageQueue` contract.

## Scope

Phase 16B adds:

- `PostgresTeamWorkerRetryStore`
- migration `docs/migrations/2026-06-12-postgres-team-worker-retries.sql`
- JSON serialization for `QueueDelivery` using existing envelope serializers
- public exports, readiness updates, and skill guidance

Out of scope:

- distributed claim/lease for due retry records
- retry daemon scheduling beyond existing `TeamWorkerDaemon`
- cancellation policy
- permission downgrade enforcement
- UI stream protocol

## Persistence Model

Table: `agentos_team_worker_retries`

Primary key:

- `(agent_id, delivery_id)`

Indexed fields:

- `team_id`
- `status`
- `next_run_at`

Payload:

- `team_id`
- `agent_id`
- `session_id`
- `delivery_id`
- `message_id`
- `attempts`
- `status`
- `next_run_at`
- `last_error`
- `delivery`
- `exhausted_at`

The adapter stores the full payload as JSONB so schema evolution can follow the
same pattern as team records and session snapshots. Top-level columns provide
query/index paths.

## Acceptance Criteria

- `PostgresTeamWorkerRetryStore.record_failure()` upserts retry records.
- `get(agent_id, delivery_id)` restores the full retry record including delivery
  envelope payload.
- `list_records(team_id=None)` returns records ordered by `next_run_at`,
  `team_id`, `agent_id`, and `delivery_id`.
- `clear(agent_id, delivery_id)` deletes one retry record.
- Migration includes up/down sections, table definition, and status/due indexes.
- Public API exports include `PostgresTeamWorkerRetryStore`.
- Readiness/docs describe persistent retry store support and keep
  cancellation, permission, and UI stream as gaps.
- Runtime loops do not import team retry store types.
