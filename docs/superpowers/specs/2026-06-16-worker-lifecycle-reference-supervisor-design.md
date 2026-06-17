# Worker Lifecycle Reference Supervisor Design

## Target Conclusion

Team workers, planner workers, and A2A push workers need one common local
process evidence boundary before production adapters are added. AgentOS should
define `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`,
and a `LocalSubprocessWorkerSupervisor` reference adapter that records
JSON-safe lifecycle evidence while staying out of Kubernetes, systemd,
autoscaling, secret distribution, and restart-policy ownership.

## Boundary

The SDK-owned boundary is intentionally small:

- `WorkerProcessSpec`: argv-only launch metadata for a local worker process.
- `WorkerProcessState`: immutable lifecycle state with `status`, `pid`,
  `exit_code`, `started_at`, `stop_requested_at`, `stopped_at`, `error`,
  `worker_kind`, `command`, `env_keys`, and metadata.
- `WorkerProcessSupervisor`: protocol for `start`, `stop`, `wait`, `state`,
  `is_running`, and `evidence`.
- `LocalSubprocessWorkerSupervisor`: reference subprocess adapter for local
  tests, prototypes, and simple supervised services.

The adapter accepts `worker_kind` values such as `team_worker`,
`planner_worker`, and `a2a_push_worker`. It uses no shell parsing, does not
serialize environment values, and returns JSON-safe lifecycle evidence.

## Non-Goals

- No Kubernetes, systemd, supervisorctl, cron, or queue worker integration.
- No restart loop, autoscaler, health probe server, or graceful drain protocol.
- No secret loader or credential distribution layer.
- No log collection or artifact storage.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Validation

- Unit tests cover spec validation, duplicate running workers, stop/wait
  evidence, failure exit codes, and environment-value redaction.
- Public API tests cover `agentos.deployment` and top-level exports.
- Docs and skill tests require the reference supervisor to be documented as a
  local adapter, not a production process-management platform.
