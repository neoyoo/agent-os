# Planner Step Recovery Design

Date: 2026-06-15

## Target Conclusion

Planner should move from "can express plan/dependencies" to "has an auditable
recovery boundary after failure." The SDK should provide step failure recording,
retry eligibility, attempt/backoff metadata, and tool entrypoints while leaving
automatic decomposition, DAG scheduling loops, worker dispatch loops, and
complex compensation to the application/profile layer.

## Design

- `PlanRetryPolicy` mirrors the existing team-worker retry semantics:
  `max_attempts`, `backoff_seconds`, `backoff_multiplier`, and
  `delay_for_attempt(attempt)`.
- `PlanStep` keeps retry audit metadata in the plan truth source:
  `attempts`, `last_failed_at`, `next_retry_at`, `retry_status`, and
  `retry_exhausted_at`.
- `PlannerRuntime.fail_step(...)` marks a step failed, records the error,
  increments attempts, and either schedules the next retry or marks retry
  exhausted.
- `PlannerRuntime.retryable_steps(plan_id)` returns failed steps whose
  dependencies are still satisfied and whose retry delay has elapsed.
- `PlannerRuntime.retry_step(...)` moves a due failed step back to `pending`,
  clears transient error/retry scheduling fields, and preserves attempt audit
  metadata.
- `PlannerTools` exposes `plan_fail_step`, `plan_retryable_steps`, and
  `plan_retry_step` so leader agents and schedulers can drive recovery without
  direct Python access.
- `plan_to_working_state_summary(...)` includes compact retry metadata but
  continues to omit task ids, workspace paths, evidence URIs, owner ids, and
  timestamps.
- `PlanStore` remains the truth source. The Postgres adapter stores retry
  metadata through the existing JSON payload, so no new migration column is
  required.

## Non-Goals

- No automatic LLM decomposition policy.
- No built-in production DAG scheduler.
- No worker process dispatch loop.
- No compensation framework for side effects.
- No QueryLoop or AsyncQueryLoop integration.

## Acceptance Criteria

- Runtime tests cover scheduled retry, due retry listing, retry reset, and
  exhausted retry.
- Tool tests cover registration and owner-scoped access for fail/retry tools.
- Serializer/Postgres tests round-trip retry metadata.
- Projection tests show compact retry metadata without execution details.
- Public API exports include `PlanRetryPolicy`.
- Readiness and SDK skill docs stop claiming planner retry policy is entirely
  future work.
