# A2A External Conformance Invocation Plan Boundary Design

Date: 2026-06-16

## Target Conclusion

Production A2A deployments need a repeatable preflight contract before running
an external compatibility or conformance suite. AgentOS should expose a
JSON-safe invocation plan and gate for suite id/version, target endpoint,
command, credentials/network/artifact/alerting policy references, and required
checks. AgentOS must not execute the external suite, manage credentials, open
network egress, provision endpoints, upload artifacts, or claim official A2A
certification.

## External Baseline

The A2A specification requires discoverable Agent Cards, protocol selection,
security schemes, HTTPS/TLS, out-of-band credential acquisition, and deployment
authorization policy. The public A2A TCK provides an executable compatibility
suite that runs against a System Under Test host and emits reports. These facts
make suite execution and certification governance deployment concerns, while
SDK-level metadata can still make the preflight plan auditable.

References:

- https://github.com/a2aproject/A2A/blob/main/docs/specification.md
- https://github.com/a2aproject/a2a-tck

## SDK Boundary

Add `A2AExternalConformanceInvocationPlan`:

- Identifies the planned external suite with `suite_id` and optional
  `suite_version`.
- Records the target endpoint and exact command tuple that deployment-owned CI
  or release automation may execute.
- Records policy references for credentials, network egress, version matrix,
  artifact retention, and failure alerting.
- Records required external check ids.
- Can project the plan into `A2AExternalConformanceExecutionRecord` after an
  external runner has already executed.
- Emits JSON-safe metadata with `no_certification_claim`.

Add `A2AExternalConformanceInvocationGateReport`:

- Evaluates whether the invocation plan contains the required preflight
  components.
- Reports missing components without attempting execution.
- Lists SDK-owned versus deployment-owned responsibilities.
- Can be used by readiness probes or release preflight checks.

## Non-Goals

- No official certification claim.
- No built-in external suite runner.
- No CI orchestration.
- No endpoint provisioning.
- No secret material or credential issuance.
- No network egress enforcement beyond existing SDK URL policy primitives.
- No artifact storage backend.

## Testing

Add focused tests that prove:

- A complete invocation plan produces a ready gate report with
  `no_certification_claim`.
- A plan missing policy references reports the correct missing components.
- A plan can project an already-executed run into
  `A2AExternalConformanceExecutionRecord`.
- Empty suite ids, targets, commands, policy references, and required checks are
  rejected.
- The new public API is exported from `agentos.channels` and top-level
  `agentos`.

## Documentation Updates

Update readiness, production readiness docs, objective audit, roadmap, and the
agent-os skill so users understand that external suite invocation planning is
now an SDK boundary, while actual execution and certification attestation remain
deployment-owned.
