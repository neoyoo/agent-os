# Worker Process Lifecycle Profile Boundary Design

## Target Conclusion

Team and planner worker processes need production-facing lifecycle readiness,
but AgentOS should not become a process supervisor. The SDK should expose a
JSON-safe deployment profile that names which worker lifecycle components are
configured, which SDK primitives can be hosted by those workers, and which
operational responsibilities remain deployment-owned.

## Current State

The SDK already owns narrow worker primitives:

- `TeamWorkerRunner`
- `TeamWorkerDaemon`
- `TeamWorkerDaemonState`
- `PlannerRuntime.scheduler_tick(...)`
- `PlanSchedulerTickReport`
- `DistributedTeamRuntimeProfile`
- `PlannerOrchestrationDeploymentProfile`
- `WorkspaceExecutionIsolationProfile`

These primitives are useful inside supervised services, cron jobs, or worker
pods. They do not define how a process is launched, restarted, drained, scaled,
credentialed, migrated, monitored, or verified against live external backends.

## Proposed Boundary

Add `WorkerProcessLifecycleDeploymentProfile` in `agentos.runtime.profile`.

The profile:

- accepts configured deployment component names
- reports missing required worker lifecycle components
- exposes `readiness_metadata()`
- exposes ASGI-compatible `readiness_check()`
- returns JSON-safe tuples/booleans/strings
- documents SDK-owned and deployment-owned responsibilities

Required components:

- `process_supervisor`
- `restart_policy`
- `graceful_shutdown`
- `health_probe`
- `readiness_probe`
- `scaling_policy`
- `credential_policy`
- `migration_policy`
- `alerting`
- `live_backend_verification`

SDK-owned responsibilities:

- `TeamWorkerRunner`
- `TeamWorkerDaemon`
- `TeamWorkerDaemonState`
- `PlannerRuntime.scheduler_tick`
- `PlanSchedulerTickReport`
- `DistributedTeamRuntimeProfile`
- `PlannerOrchestrationDeploymentProfile`
- `WorkspaceExecutionIsolationProfile`
- readiness-compatible profile payloads

Deployment-owned responsibilities:

- process supervisor or job runner
- restart policy
- graceful shutdown and draining
- horizontal scaling policy
- credentials and secret distribution
- schema migration execution
- live backend verification
- health/readiness endpoint wiring
- alert routing and runbooks
- OS/container sandboxing

## Non-Goals

- No systemd, Kubernetes, process-manager, queue-worker, or cron integration.
- No long-running scheduler loop in `PlannerRuntime`.
- No worker autoscaler.
- No distributed lock manager.
- No credential loader or secret manager.
- No database migration runner.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Validation

- Runtime profile tests prove missing/ready metadata and input validation.
- Public API tests prove exports from `agentos.runtime` and top-level
  `agentos`.
- Readiness/docs tests prove team and planner forms name the profile while
  keeping process supervision and worker scaling deployment-owned.
- Runtime boundary scan proves worker lifecycle concepts do not leak into
  query loops.
