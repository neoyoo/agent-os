# Distributed Team Profile Preset Design

## Target Conclusion

Distributed team agents should not require every application to hand-wire team
stores, message queues, worker session providers, retry and cancellation
stores, UI streams, and worker daemon policies from low-level primitives. The
SDK should provide a production-oriented team profile preset that assembles
these boundaries and exposes readiness metadata, while deployment still owns
credentials, migrations, process supervision, worker scaling, and
OS/container sandboxing.

## Problem

`TeamRuntime`, `TeamWorkerRunner`, `TeamWorkerDaemon`, Postgres-backed team
stores, persistent retry/cancellation stores, and distributed UI streams exist
as SDK primitives. This is enough for custom applications, but it leaves the
standard distributed team agent shape under-specified:

- The team runtime and worker runner can accidentally use different queues or
  UI stream stores.
- The SDK has no single object that states which team adapters are configured.
- Readiness guidance says the form is primitives-ready, but the recommended
  profile is still the generic `DistributedAgentProfile`.
- Applications must repeatedly rediscover the same assembly order.

## Scope

This phase adds a narrow preset for the team discussion form. It does not add a
production scheduler, process supervisor, worker scaler, migration runner, or
container sandbox.

## Proposed API

Add `DistributedTeamRuntimeProfile` in `agentos.runtime.profile`.

The profile accepts concrete adapter instances:

- `store`
- `message_queue`
- `worker_session_provider`
- optional `worker_agent_provider`
- optional `retry_policy`
- optional `retry_store`
- optional `cancellation_store`
- optional `ui_stream`
- optional `notice_store`
- optional `wakeup_trigger`
- daemon `team_id` and polling interval

It assembles:

- `team_runtime`
- optional `worker_runner`
- optional `worker_daemon`

It exposes:

- `build_team_runtime()`
- `build_team_tools(owner_agent_id=...)`
- `build_worker_runner()`
- `build_worker_daemon()`
- `readiness_checks()`
- `readiness_metadata()`

`build_agent(...)` raises `NotImplementedError`, because the preset coordinates
team infrastructure rather than constructing a single conversational agent.

## Boundaries

The preset may import team runtime classes inside construction methods, but
`QueryLoop` and `AsyncQueryLoop` must remain unaware of team, planner, A2A, or
profile concepts.

The preset owns only SDK composition. It must not:

- create Redis/Postgres connections from credentials,
- run migrations,
- start daemon threads automatically,
- supervise worker processes,
- choose worker scaling policy,
- enforce OS/container sandboxing.

## Testing

Tests should prove:

- the profile assembles one coherent `TeamRuntime`, `TeamWorkerRunner`, and
  `TeamWorkerDaemon` from injected adapters,
- the assembled runtime can create a team, create an independent worker
  session, enqueue a team message, and process it through the daemon,
- readiness metadata names the configured adapters and remaining production
  gaps,
- public exports exist from `agentos.runtime` and top-level `agentos`,
- runtime loops do not import team profile or team worker classes.
