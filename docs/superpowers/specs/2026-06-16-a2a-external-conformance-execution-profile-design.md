# A2A External Conformance Execution Profile Design

## Target Conclusion

A2A interoperability should not rely only on SDK self-conformance, but the SDK
must not claim certification by embedding or pretending to run an external
suite. AgentOS needs a deployment-facing profile that accepts external
conformance reports, names the required checks and deployment components, and
turns that evidence into readiness metadata.

## Current State

The SDK already has:

- `A2AConformanceHarness` for local SDK self-conformance checks.
- `A2AExternalConformanceReportImporter` for normalizing external result JSON.
- `A2AConformanceReport` and `A2AConformanceFinding` as report models.
- Readiness/docs that still list external conformance execution as a major A2A
  gap.

The missing boundary is not parsing. It is an execution readiness contract:
which external suite result is attached, whether the required checks passed,
and which deployment-owned pieces must be configured before a deployment can
use that result for production gates.

## Proposed Boundary

Add `A2AExternalConformanceExecutionProfile` to
`agentos.channels.a2a_conformance`.

The profile:

- accepts an optional `A2AConformanceReport`
- accepts required external check ids
- accepts configured deployment component names
- reports missing required checks
- reports failed required checks
- reports missing execution components
- exposes `readiness_metadata()`
- exposes ASGI-compatible `readiness_check()`
- returns JSON-safe payloads suitable for readiness endpoints and deployment
  specs

Default required components:

- `external_suite_runner`
- `target_endpoint`
- `credential_policy`
- `network_egress_policy`
- `version_matrix`
- `ci_artifact_retention`
- `failure_alerting`

Default required external checks:

- `agent-card`
- `message-send`
- `message-stream`
- `tasks-resubscribe`
- `push-notification-config`

## SDK-Owned Responsibilities

- external report import and normalization
- required-check gating
- failed-check projection
- JSON-safe metadata
- ASGI readiness-compatible payloads
- no certification claim

## Deployment-Owned Responsibilities

- external suite execution
- target environment provisioning
- credential issuance and secret distribution
- network egress policy
- CA trust rollout
- version matrix selection
- CI artifact retention
- failure alerting and release gating
- certification program or vendor-specific attestation

## Non-Goals

- No subprocess runner.
- No network calls.
- No external suite packaging.
- No official certification claim.
- No QueryLoop or AsyncQueryLoop changes.

## Validation

- Unit tests verify missing report, missing components, failed required checks,
  ready metadata, and input validation.
- Public API tests verify export from `agentos.channels` and top-level
  `agentos`.
- Readiness/docs tests verify the A2A form now names the execution profile and
  removes SDK-owned external suite execution from the remaining gap.
- Runtime boundary scan proves the profile does not leak into query loops.

