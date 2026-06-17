# Distributed Team Store Design (Phase 12A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 5A team runtime/wakeup and Phase 9A team tools

## Target Conclusion

```text
Team agents are not just function calls inside a leader agent; they are
persistent leader/worker session boundaries. The SDK needs a distributed
TeamStore adapter before team discussion can be a credible multi-node pattern.
```

`TeamRuntime` already has the right domain boundary: teams, members, messages,
visibility, and wakeup hints. The remaining production gap is the store
implementation. In-memory state is fine for tests and local prototypes, but a
web or worker fleet needs one shared truth source that every node can read and
write.

## Scope

Add a Postgres-backed implementation of the existing `TeamStore` protocol:

- `PostgresTeamStore`
- schema migration for team records, members, and messages
- serializers for `TeamRecord`, `TeamMemberRecord`, and `WorkspaceHandle`
- tests with a fake Postgres connection, following `PostgresTaskStore`
- public exports and readiness/docs updates

This phase does not add an automatic worker session runner. The lifecycle of
leader and worker sessions remains an explicit SDK boundary for a later phase.

## Architecture

`PostgresTeamStore` lives under `agentos.multi`, next to
`PostgresTaskStore`. It depends only on:

- `agentos.multi.team` dataclasses and `TeamStore` semantics
- `agentos.multi.serializers`
- the minimal `PostgresConnection` protocol
- `BackendUnavailableError`

It does not import runtime loops, web channels, planners, or A2A operation
servers.

The adapter keeps one table per team state shape:

- `agentos_team_records`
- `agentos_team_members`
- `agentos_team_messages`

Each row stores queryable identity/status columns plus a JSONB `payload` that
round-trips the SDK dataclass. This matches the existing task-store approach:
stable columns for common selectors, JSONB payload for SDK evolution.

## Data Semantics

### Teams

`create_team(team)` inserts a team row and raises on duplicate primary keys via
the database. `get_team(team_id)` reads the JSONB payload. `mark_team_deleted`
updates the status column and patches the JSONB payload with `status:
"deleted"`. `now` is accepted to match the `TeamStore` protocol, but the
current `TeamRecord` does not yet store a deletion timestamp.

### Members

`add_member(member)` first confirms the team exists and is active, then
upserts the member row by `(team_id, agent_id)`. This mirrors the in-memory
store behavior where adding an existing member replaces the record.

`list_members(team_id)` returns records ordered by `created_at, agent_id` so
multi-node reads are deterministic.

### Messages

`append_message(message)` first confirms the team exists and is active, then
inserts the message row. Messages are ordered by `created_at, message_id`.

`list_messages(team_id, agent_id=None, after_message_id=None)` reads messages
from the shared store and applies the same visibility rule as
`InMemoryTeamStore`:

- broadcast messages are visible to everyone except the sender
- directed messages are visible only to `to_agent_id`
- when `agent_id` is `None`, every message is returned

Cursor behavior stays compatible with the in-memory implementation:
`after_message_id` returns messages after the matching message; if the cursor
is missing, all messages are returned.

## Workspace Serialization

Teams and members can carry `WorkspaceHandle` values. The store serializes the
handle to JSONB inside the dataclass payload:

- `workspace_id`
- `scope`
- `root`
- `parent_workspace_id`
- `metadata`

The adapter stores the handle as metadata only. It does not enforce filesystem
sandboxing or permission downgrade.

## Production Boundary

For a distributed team deployment, the recommended state topology becomes:

- `PostgresTeamStore` for team records, members, and messages
- `RedisAgentMessageQueue` for delivery/wakeup hints
- `TeamRuntime` for membership and visibility semantics
- app/profile-owned worker session lifecycle
- app/profile-owned workspace narrowing and sandbox enforcement

`AgentMessageQueue` remains notification-only. Team messages must be read from
`TeamStore`, not drained from a queue.

## Non-Goals

- No worker session creation or scheduler.
- No planner/team auto-integration.
- No UI stream protocol.
- No message outbox or delivery reconciliation for team messages.
- No row-level authorization policy.
- No runtime-loop imports.

## Acceptance Criteria

- `PostgresTeamStore` implements the existing `TeamStore` protocol.
- Teams, members, messages, status changes, cursor reads, and visibility rules
  round-trip through a fake Postgres connection.
- Workspace handles round-trip for teams and members.
- A migration creates the three Postgres tables and indexes.
- `agentos.multi.PostgresTeamStore` and top-level `agentos.PostgresTeamStore`
  are exported lazily.
- Team readiness no longer lists distributed TeamStore as required app glue,
  but still lists worker session lifecycle and permission downgrade policy.
- Docs and SDK skill guidance say production clusters can use
  `PostgresTeamStore` with `RedisAgentMessageQueue`.
- Runtime loops remain free of team-store/persistence imports.
