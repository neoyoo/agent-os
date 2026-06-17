# Production Readiness Matrix Design (Phase 7)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phases 0-6C architecture review slices

## Target Conclusion

```text
agent-os must describe each agent form with production readiness evidence, not
only with capability labels such as direct or primitives-ready.
```

The SDK is becoming a foundation for agent development. That means "supported"
must be inspectable across production dimensions: state recovery, concurrency,
auth, rate limiting, timeout, retry, observability, workspace isolation, schema
migration, and protocol parity.

## Scope

Add a small, static readiness matrix module:

- `agentos.readiness`
- `ReadinessLevel`
- `ReadinessDimension`
- `AgentFormReadiness`
- `get_agent_form_readiness(form_id)`
- `list_agent_form_readiness()`

The matrix is not a runtime health checker. It is a versioned SDK capability map
that docs, skills, tests, and later scaffolding can use as a shared source of
truth.

## Initial Forms

The first slice should cover forms that are central to the current review:

- `terminal-script`
- `async-web-host`
- `web-distributed-session`
- `a2a-discovery`
- `team-discussion`
- `planner-intent-router`

Additional direct forms can be added later, but these are enough to prevent the
largest production-readiness misread.

## Dimension Semantics

Each dimension has:

- `name`
- `level`: `direct`, `primitives-ready`, `future-extension`, or
  `not-applicable`
- `evidence`: stable module/test/doc evidence
- `gap`: remaining work, empty only when the dimension is direct or not
  applicable

Required dimensions:

- session_state
- concurrency
- auth
- rate_limit
- timeout
- retry
- observability
- workspace
- protocol
- persistence
- schema_migration

## Production Classification

Each form has:

- `form_id`
- `name`
- `overall_level`
- `summary`
- `dimensions`
- `recommended_profile`
- `required_app_glue`

The overall level must not be stronger than its weakest required dimension. For
example, web distributed session support remains `primitives-ready` until
distributed lease/snapshot adapters are concrete SDK implementations.

## Non-Goals

- No dynamic probing of live Redis/Postgres/A2A endpoints.
- No change to runtime loop behavior.
- No automatic deployment generator.
- No attempt to complete all production gaps in this phase.

## Acceptance Criteria

- Matrix exposes the six initial forms with required dimensions.
- Terminal/script is classified as direct.
- Web distributed session is classified as primitives-ready and names the
  distributed lease/snapshot adapter gap.
- A2A discovery is classified as primitives-ready and explicitly does not claim
  full A2A task/message parity.
- Team discussion is classified as primitives-ready and names worker lifecycle
  and distributed team store gaps.
- Planner/intent-router is classified as primitives-ready and names
  decomposition/DAG/scheduler/compensation gaps while recognizing
  `PostgresPlanStore` and `PlanRetryPolicy` as SDK primitives.
- Public exports are available from top-level `agentos`.
- Skill docs reference the readiness matrix for production planning.
- `QueryLoop` and `AsyncQueryLoop` remain free of readiness imports.
