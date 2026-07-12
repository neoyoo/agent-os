# Production Readiness Evidence Bundle Boundary Design

## Target Conclusion

Production AgentOS releases need one SDK-owned release gate evidence bundle that
combines already-produced readiness/profile/backend evidence into a JSON-safe
decision payload. AgentOS should report accepted, blocking checks, missing
required checks, and the sdk_owned/deployment_owned split without executing real
infrastructure checks.

## Scope

The SDK owns:

- `ProductionReadinessEvidenceBundle`
- `ReadinessEvidenceCheck`
- `ReadinessEvidenceStatus`
- `accepted`
- `blocking_checks`
- `missing_required_checks`
- `block_production_readiness`
- JSON-safe evidence bundle serialization
- redaction of secret-like metadata keys
- normalization of existing readiness/profile/backend evidence

Deployment owns:

- backend check execution
- provider/backend credentials
- network, TLS, gateway, and tenant policy
- migration execution
- CI matrix execution
- artifact retention
- release approval, rollout, rollback, alerting, and runbooks

## Boundary

`ProductionReadinessEvidenceBundle` consumes existing
readiness/profile/backend evidence. It does not execute real infrastructure
checks, does not connect to Nacos, Redis, Postgres, Kubernetes, systemd, Docker,
or E2B, and does not certify a release by itself.

The bundle accepts mapping sources, callable sources, or objects exposing
`readiness_check`, `readiness_metadata`, or `as_dict`. Each source is normalized
into a `ReadinessEvidenceCheck` with a `ReadinessEvidenceStatus` of `passed`,
`failed`, `skipped`, or `unknown`.

## Acceptance

A bundle is accepted only when every required check is present and no required
check blocks production readiness. Missing required checks are reported in
`missing_required_checks`; failed, skipped, or unknown required checks are
reported in `blocking_checks`.

