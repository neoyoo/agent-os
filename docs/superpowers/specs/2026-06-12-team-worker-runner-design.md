# Team Worker Runner Design (Phase 14A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 13A worker session lifecycle

## Target Conclusion

```text
A team worker session is only useful if a distributed runner can turn team
message wakeups into worker continuation turns. The SDK should provide this
runner boundary without coupling QueryLoop to team semantics.
```

AgentScope2 treats team workers as independent sessions that can be spawned,
messaged, and woken by distributed dispatchers. agent-os now has the state
pieces: `TeamRuntime`, `TeamStore`, `PostgresTeamStore`, `TeamTools`, and
`TeamWorkerSessionProvider`. The missing execution primitive is a small runner
that consumes team-message deliveries and drives the recipient worker session.

## Scope

Add a sync, testable runner boundary:

- `TeamWorkerAgentProvider`
- `TeamWorkerRunResult`
- `TeamWorkerRunError`
- `TeamWorkerRunner`

The runner:

1. Lists worker sessions from a `TeamWorkerSessionProvider`.
2. Collects deliveries for each worker agent from `AgentMessageQueue`.
3. Ignores non-`team_message` envelopes so task dispatch stays separate.
4. Resolves the worker agent through `TeamWorkerAgentProvider`.
5. Calls `agent.run_continuation()` for `team_message` deliveries.
6. Acks successful deliveries.
7. Records failures and leaves failed deliveries unacked.

## Architecture

This lives in `agentos.multi.team` for now because it is tightly coupled to
team lifecycle records and notices. It does not import web channels, A2A
operations, Redis details, Postgres details, or runtime loop modules.

`TeamWorkerRunner` depends on protocols:

- `TeamWorkerSessionProvider`
- `TeamWorkerAgentProvider`
- `AgentMessageQueue`

It intentionally does not build agents. A production host can resolve worker
sessions through `DurableAgentSessionProvider`, local maps, or a custom session
fleet. The runner only orchestrates one unit of work.

## Failure Semantics

Successful team-message delivery:

- worker agent runs a continuation turn
- queue delivery is acked
- result is recorded as `completed`

Unsupported or irrelevant delivery:

- non-`team_message` envelopes are ignored
- the delivery is left unacked, because another consumer may own that type

Failure:

- error is recorded as `TeamWorkerRunError`
- delivery is not acked
- Redis pending/reclaim or app policy may retry later

This keeps the phase small and avoids inventing retry/backoff policy before the
execution boundary is proven.

## Non-Goals

- No thread pool or daemon wakeup dispatcher.
- No async runner.
- No retry/backoff scheduler.
- No cancellation handling.
- No automatic durable worker-session adapter.
- No permission enforcement beyond carrying the session/workspace handle.
- No runtime-loop imports.

## Acceptance Criteria

- Runner executes pending team-message deliveries for active worker sessions.
- Runner calls `agent.run_continuation()` exactly once per successful delivery.
- Runner acks successful team-message deliveries.
- Runner ignores task envelopes and leaves them unacked.
- Runner records failures and leaves failed team-message deliveries unacked.
- Public exports include runner types.
- Readiness/docs/skill guidance remove "worker execution runner" from required
  app glue. At Phase 14A, daemon dispatch, retry/backoff, cancellation,
  permission policy, and UI stream protocol remain gaps; later phases add
  daemon hosting and retry/backoff primitives.
- Runtime loops remain free of team runner imports.
