# A2A External Conformance Runner Boundary Design

## Target Conclusion

A2A interoperability should be verifiable through an external suite, but
AgentOS should only own the stable SDK boundary that invokes a deployment-chosen
suite and records local evidence. The SDK should provide a reference CLI runner
that turns an `A2AExternalConformanceInvocationPlan` into an
`A2AExternalConformanceExecutionRecord`, optionally importing report JSON, while
leaving the suite, CI system, certification authority, network policy, secrets,
artifact storage, and live target verification deployment-owned.

This phase improves the future external A2A agent shape by making conformance
execution repeatable and auditable without turning AgentOS into a protocol
certification platform.

## Current State

AgentOS already has:

- `A2AExternalConformanceReportImporter` for external JSON report import.
- `A2AExternalConformanceInvocationPlan` and
  `A2AExternalConformanceInvocationGateReport` for preflight planning.
- `A2AExternalConformanceExecutionRecord` and
  `A2AExternalConformanceGateReport` for execution evidence and local release
  gating.
- `A2AExternalConformanceExecutionProfile` for readiness metadata.

The missing SDK-owned piece is a reference adapter that actually runs a CLI
command from an invocation plan and produces the existing execution record.

## SDK-Owned Boundary

- Define `A2AExternalConformanceRunner` as a protocol with `run(plan)`.
- Provide `A2AExternalConformanceCliRunner` as a reference adapter.
- Invoke `plan.command` with argv-only subprocess execution and no shell
  parsing.
- Capture start/end timestamps, exit code, bounded stdout/stderr summaries,
  environment label, artifact URI, report import source, timeout configuration,
  and JSON-safe runner metadata.
- Import report JSON from either a configured report path or stdout JSON.
- Capture process timeout as a nonzero execution record instead of making a
  certification claim.
- Record environment variable names only when caller passes environment
  overrides; secret values must not appear in evidence.

## Deployment-Owned Boundary

- External suite selection, installation, versioning, and maintenance.
- CI/CD wiring, matrix selection, artifact upload, retention, and release
  policy.
- Target endpoint provisioning, credentials, secret distribution, network egress
  enforcement, DNS pinning, CA trust rollout, enterprise proxying, and tenant
  directory lifecycle.
- Certification program enrollment, vendor attestation, and audit governance.
- Live backend verification and production traffic gating.

## API Shape

```python
runner = A2AExternalConformanceCliRunner(
    timeout_seconds=60,
    report_path=Path("a2a-report.json"),
    environment="ci",
    artifact_uri="s3://ci/a2a/report.json",
)

record = runner.run(plan)
gate = A2AExternalConformanceGateReport.from_record(
    record,
    required_check_ids=plan.required_check_ids,
    required_components=plan.required_components,
    configured_components=plan.configured_component_names(),
)
```

## Definition Of Done

- Runner API is exported from `agentos.channels` and top-level `agentos`.
- CLI runner returns `A2AExternalConformanceExecutionRecord` for success,
  nonzero exit, malformed report, missing report, and timeout.
- No subprocess call uses shell parsing.
- Evidence is JSON-safe and includes `no_certification_claim`.
- Environment evidence contains labels and env keys, not secret values.
- Production readiness docs, objective coverage audit, roadmap, and agent-os
  skill guidance name the runner boundary and deployment-owned exclusions.
- Runtime query loops remain free of A2A conformance runner concepts.

## Testing

- Unit tests for successful CLI execution importing report JSON from a file.
- Unit tests for stdout JSON report import.
- Unit tests for nonzero exit code evidence.
- Unit tests for malformed or missing report evidence without process failure
  being hidden.
- Unit tests for bounded stdout/stderr summaries.
- Unit tests for timeout evidence and invalid runner configuration.
- Public API export tests.
- Docs tests for production readiness and objective coverage audit.

