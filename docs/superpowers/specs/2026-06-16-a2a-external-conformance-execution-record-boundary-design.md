# A2A External Conformance Execution Record Boundary Design

## Target Conclusion

External A2A interoperability needs repeatable execution evidence, but AgentOS
should not embed an official suite runner, CI system, certification authority,
credential issuer, or network trust controller. The SDK should expose a
JSON-safe execution record and gate report boundary that lets deployments attach
already-executed external suite metadata to imported `A2AConformanceReport`
results, then evaluate release readiness without making a certification claim.

## Scope

This phase adds SDK-owned data structures for recording external conformance
suite execution attempts and projecting them into readiness/release-gate
metadata. It does not invoke subprocesses, manage CI jobs, provision target
environments, issue credentials, pin DNS, roll out CA trust, upload artifacts,
or attest certification.

## SDK-Owned Boundary

- Normalize external suite execution metadata into an immutable
  `A2AExternalConformanceExecutionRecord`.
- Capture command, target endpoint, environment name, exit code, started/ended
  timestamps, stdout/stderr summaries, artifact URI, and imported
  `A2AConformanceReport` evidence.
- Produce an `A2AExternalConformanceGateReport` that composes execution success,
  imported report pass/fail state, required-check presence, failed-check
  projection, and optional configured deployment components.
- Return JSON-safe payloads for CI logs, readiness probes, release gates, and
  objective coverage audits.
- Explicitly state that a passed gate is evidence for a local release policy,
  not a protocol certification claim.

## Deployment-Owned Boundary

- External suite selection, installation, invocation, and retries.
- CI pipeline wiring, artifact upload, environment matrix, and release policy.
- Target endpoint provisioning, credentials, secrets, network egress, DNS
  pinning, CA rollout, enterprise proxying, and tenant directory lifecycle.
- Certification program enrollment, vendor attestation, and audit governance.

## API Shape

```python
record = A2AExternalConformanceExecutionRecord(
    suite="official-a2a-conformance",
    target="https://agents.example/a2a",
    command=("a2a-conformance", "--target", "https://agents.example/a2a"),
    exit_code=0,
    started_at=100.0,
    ended_at=110.5,
    environment="ci",
    artifact_uri="s3://ci/a2a/report.json",
    report=imported_report,
)

gate = A2AExternalConformanceGateReport.from_record(
    record,
    required_check_ids=("agent-card", "message-send"),
    configured_components=("external_suite_runner", "target_endpoint"),
    required_components=("external_suite_runner", "target_endpoint"),
)

gate.ready
gate.as_dict()
```

## Testing

- Unit tests for successful execution records producing ready gate reports.
- Unit tests for failed exit codes, missing reports, failed checks, and missing
  required checks.
- Unit tests for validation of empty suite/target/command names and invalid
  timestamps.
- Public API export tests from `agentos.channels` and top-level `agentos`.
- Readiness/docs tests that keep the boundary visible in the long-running
  objective audit.

