# Team Worker Session Lifecycle Design (Phase 13A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 5A team runtime, Phase 9A team tools, Phase 12A distributed TeamStore

## Target Conclusion

```text
Team workers are independent sessions, not only member records. agent-os needs
an explicit worker session lifecycle boundary before team discussion can become
a production-grade distributed agent pattern.
```

AgentScope2's team shape is distributed by default: leader and worker sessions
can live in different processes or nodes; messages go through a Redis-backed
bus/inbox; any wakeup dispatcher can claim the signal and drive the recipient
session. agent-os already has team records/messages, distributed team state, and
queue wakeup hints. The missing SDK boundary is the lifecycle step that turns
`agent_create` from "write a member record" into "create/register a worker
session and then write the member record."

## Scope

Add a minimal worker session lifecycle boundary:

- `TeamWorkerSessionRequest`
- `TeamWorkerSession`
- `TeamWorkerSessionProvider`
- `InMemoryTeamWorkerSessionProvider`
- `TeamRuntime(worker_session_provider=...)`
- `TeamTools.agent_create` wired through the runtime boundary
- close worker sessions when a team is deleted

This phase does not run workers automatically. It creates and closes session
records and keeps message delivery as the existing wakeup hint path.

## Architecture

`TeamRuntime.add_member()` becomes the lifecycle coordination point:

1. Require the team to be active.
2. If the member is a worker and a `TeamWorkerSessionProvider` exists, call
   `create_worker_session()` with the team/member/workspace request.
3. Use the returned `session_id` and `workspace` on `TeamMemberRecord`.
4. Persist the member in `TeamStore`.
5. Create the member inbox in `AgentMessageQueue`.

`TeamRuntime.delete_team()` closes active worker sessions through the provider
before marking the team deleted. Close is best-effort and idempotent from the
SDK perspective; the provider owns external cleanup details.

## Data Model

### `TeamWorkerSessionRequest`

Fields:

- `team_id`
- `agent_id`
- `role`
- `capabilities`
- `requested_session_id`
- `team_workspace`
- `requested_workspace`
- `created_by_agent_id`
- `created_at`

### `TeamWorkerSession`

Fields:

- `team_id`
- `agent_id`
- `session_id`
- `status`: `created | closed`
- `workspace`
- `capabilities`
- `created_at`
- `closed_at`

The session object is a lifecycle handle. It is not an `Agent` instance and it
does not contain context/message runtime state.

## In-Memory Provider

`InMemoryTeamWorkerSessionProvider` is for tests, local prototypes, and spec
examples. It:

- creates stable session ids when the caller does not provide one
- stores sessions by `(team_id, agent_id)`
- returns created sessions for inspection
- marks sessions closed on delete

The provider does not instantiate an agent or run a loop.

## Team Tools

`agent_create` continues to expose the same LLM-facing operation. When the
runtime has a worker session provider, the returned member JSON includes the
provider-generated `session_id`. This lets agent specs use `agent_create`
without knowing whether the deployment uses a local or distributed session
provider.

## Non-Goals

- No automatic wakeup dispatcher.
- No worker run loop.
- No retry or cancellation scheduler.
- No durable worker-session database adapter.
- No permission downgrade enforcement beyond carrying the workspace handle.
- No runtime-loop imports.

## Acceptance Criteria

- Adding a worker through `TeamRuntime.add_member()` calls the provider and
  stores the generated `session_id`.
- Supplying an explicit `session_id` passes that id through the provider.
- Leader members do not create worker sessions.
- Deleting a team closes active worker sessions through the provider.
- `TeamTools.agent_create` returns the generated worker session id.
- Public exports include the worker session lifecycle types.
- Readiness/docs/skill guidance no longer list "worker session lifecycle" as
  missing, but still list worker execution runner, permission policy, and UI
  stream protocol as gaps.
- Runtime loops remain free of team lifecycle imports.
