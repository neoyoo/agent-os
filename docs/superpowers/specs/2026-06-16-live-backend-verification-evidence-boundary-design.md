# Live Backend Verification Evidence Boundary Design

## Target Conclusion

Production AgentOS deployments must not claim a backend is production-ready
only because a profile names it. Nacos, Redis, Postgres, worker supervisors,
session snapshot persistence, and sandbox/workspace execution backends need
deployment-owned live verification evidence before readiness or release gates
can pass. AgentOS should consume JSON-safe verification records, report missing
or failed backend checks, and block production readiness when evidence is absent
or failed. The SDK must not connect to live backends, hold credentials, execute
migrations, run CI, own alerting, or replace deployment runbooks.

## Scope

This phase adds a generic SDK evidence boundary in `agentos.deployment`.

The SDK owns:

- stable verification record fields
- JSON-safe payloads
- secret-key rejection in metadata
- required-backend coverage checks
- pass/fail/skipped/unknown gate reporting
- a readiness-compatible deployment profile
- public exports and guidance

The deployment owns:

- concrete Nacos, Redis, Postgres, Docker, E2B, Kubernetes, or systemd checks
- credentials and secret distribution
- migration execution
- network policy and TLS trust rollout
- CI matrix execution
- alert routing and runbooks
- certification or release approval

## API Shape

`BackendVerificationRecord` represents one deployment-owned backend check.
It carries a backend name, backend kind, status, checked timestamp, evidence
reference, optional target reference, optional error, and metadata. Metadata is
accepted only when it is JSON-safe and does not contain obvious secret-bearing
keys.

`DeploymentLiveBackendVerificationGateReport` evaluates a set of records
against required backend names. It reports missing, failed, skipped, and unknown
backends. The report sets `block_production_readiness=True` unless every
required backend has a passed record.

`DeploymentLiveBackendVerificationProfile` wraps the same gate in a
readiness-compatible `readiness_metadata()` and `readiness_check()` shape.

`LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS` defines the default required
backend names for the production state plane:

- `agent_registry`
- `message_queue`
- `task_store`
- `plan_store`
- `worker_process_supervisor`
- `session_snapshot_persistence`

## Invariants

- Records never emit environment values, tokens, passwords, API keys, DSNs, raw
  configs, or connection strings.
- Missing evidence references are invalid.
- Failed, skipped, and unknown records block production readiness for required
  backends.
- Extra records may be included as audit evidence, but they do not satisfy a
  missing required backend.
- Query loops must not import or reference this boundary.

## Tests

- Record tests cover JSON-safe payloads and secret metadata rejection.
- Gate tests cover all-passed, missing, failed, skipped, and unknown records.
- Profile tests cover readiness payload shape.
- Public API tests cover `agentos.deployment` and top-level `agentos` exports.
- Docs tests cover production readiness, objective audit, roadmap, and skill
  guidance.
