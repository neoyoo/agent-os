# Live Backend Verification Reference Runner Boundary Design

## Target Conclusion

Production AgentOS deployments need a repeatable way to collect live backend
verification evidence without moving Nacos, Redis, Postgres, supervisor, or
session snapshot clients into the SDK. AgentOS should provide an argv-only
reference runner that invokes deployment-owned check scripts, imports a report
path or stdout JSON, and returns JSON-safe evidence for readiness gates. The
runner is not a live backend client and makes no backend client claim.

## Scope

This phase extends `agentos.deployment` with a reference invocation boundary.

The SDK owns:

- `BackendVerificationInvocationPlan` as an argv-only command contract
- `BackendVerificationReportImporter` and
  `BackendVerificationReportImportError` for report normalization
- `BackendVerificationRunner` as a protocol
- `BackendVerificationCliRunner` as the local reference runner
- `DeploymentLiveBackendVerificationRunResult` as JSON-safe execution evidence
- bounded stdout/stderr summaries, `env_keys`, and secret value redaction

The deployment owns:

- backend check script implementation
- credentials and secret distribution
- Nacos, Redis, Postgres, Docker, E2B, Kubernetes, or systemd clients
- network and TLS policy
- migration execution
- CI matrix execution
- alert routing and runbooks
- release approval and certification

## API Shape

`BackendVerificationInvocationPlan` carries an argv tuple and required backend
names. It does not accept shell strings, shell parsing, or secret-bearing
metadata.

`BackendVerificationReportImporter` accepts JSON objects with a non-empty
`records` list. Records may use snake_case or common camelCase aliases such as
`backendName`, `backendKind`, `checkedAt`, and `evidenceRef`. Status values
normalize to `passed`, `failed`, `skipped`, or `unknown`.

`BackendVerificationCliRunner` runs the invocation plan with `shell=False`,
captures stdout/stderr, bounds summaries, redacts configured environment
values, records only `env_keys`, and imports either `report_path` or stdout JSON.
Timeout, nonzero exit, missing report, or malformed report returns a run result
that blocks production readiness.

`DeploymentLiveBackendVerificationRunResult` combines execution evidence with
`DeploymentLiveBackendVerificationGateReport`. It accepts readiness only when
the process exits with code 0 and every required backend has a passed record.

## Invariants

- The runner never stores environment values in evidence.
- The runner never parses shell strings.
- The runner never connects to backends directly.
- A successful process without required backend records still blocks production
  readiness.
- Query loops must not import or reference this boundary.

## Tests

- Importer tests cover canonical JSON and common field aliases.
- CLI runner tests cover report path import, stdout JSON import, timeout,
  nonzero exit, malformed report, output bounding, env key redaction, and
  invalid settings.
- Public API tests cover `agentos.deployment` and top-level `agentos` exports.
- Docs tests cover production readiness, objective audit, roadmap, and skill
  guidance.
