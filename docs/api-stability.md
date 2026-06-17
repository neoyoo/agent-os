# AgentOS API Stability

Canonical path: `docs/api-stability.md`.

## Target Conclusion

Phase 100: Release Hardening requires an API stability classification so a
release reviewer can tell which public SDK surfaces are stable API and which are
experimental API. This document is release candidate evidence and is enforced by
the public API audit in `tests/architecture/test_public_api.py`.

The machine-readable public API inventory is `docs/public-api-inventory.json`.
It records every governed `__all__` export for the release-audited public
modules, their stable or experimental classification, and their current
normalized `inspect.signature` string where Python can expose one. The inventory
is not a replacement for this policy document: this document explains
compatibility rules, while the JSON file gives review tooling a concrete
signature baseline.

## Governed Stable Namespaces

Every namespace listed here must be represented in
`docs/public-api-inventory.json`:

- `agentos`
- `agentos.channels`
- `agentos.multi`
- `agentos.runtime`
- `agentos.workspace`
- `agentos.registry`
- `agentos.deployment`
- `agentos.readiness`
- `agentos.release`

## Stable API

Stable API should be safe for production specs to depend on with normal
deprecation and migration notes before incompatible change:

- `AgentBuilder`, `Agent`, `QueryLoop`, `AsyncQueryLoop`, and
  `ProviderRequestBuilder`
- provider protocols and typed provider messages
- context runtime, message runtime, context protocol tools, compression, hooks,
  and event bus surfaces
- `ToolCallRouter`, `ToolRegistry`, `RegisteredTool`, and MCP/skill loading
  boundaries
- `AsgiAgentApp`, durable session provider protocols, session lease protocols,
  and session snapshot persistence protocols
- `RuntimeProfile`, `LocalRuntimeProfile`, `WebRuntimeProfile`,
  `DistributedWebRuntimeProfile`, and distributed session operation profiles
- `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`,
  `ReadinessEvidenceStatus`, and `agentos.readiness` form records
- release evidence validation surfaces: `RELEASE_EVIDENCE_REQUIRED_GATES`,
  `ReleaseEvidenceValidationReport`, `ReleaseEvidenceGateStatus`, and
  `validate_release_evidence_manifest`. The release-candidate validation path
  `validate_release_candidate_evidence_manifest` is also stable and requires
  expected branch, commit, and version identity for actual release gates.
- workspace protocol boundaries such as `WorkspaceExecutionBackend`,
  `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`,
  `WorkspaceExecutionPolicy`, and `LocalWorkspaceExecutionBackend`
- plan-store concurrency boundaries: `CompareAndSavePlanStore`,
  `PlanStoreRecord`, and `PlanConflictError`. These define optimistic
  concurrency control for shared `PlanStore` mutation and are stable because
  planner/team workers need a reliable plan-store concurrency contract across
  nodes.

## Experimental API

Experimental API is production-useful but may change while the release line
hardens. Changes must be noted in `CHANGELOG.md` and, when schema or state
changes are involved, in migration notes.

- advanced A2A operation and conformance surfaces
- Nacos registry adapter behavior beyond AgentCard metadata projection
- team runtime UI stream and worker daemon profiles
- planner scheduler daemon, claimed scheduler, stale claim sweep, and LLM
  governance profiles
- `ReferenceStatePlaneStack` and `ReferenceLiveBackendProbePack`
- worker supervisor reference adapters
- release evidence helpers that aggregate external backend probe output

## Compatibility Rules

- Stable API changes require a deprecation path or migration notes.
- Experimental API changes require changelog entries and test updates.
- boundary-first ownership stays unchanged: SDK API exposes protocols, profiles,
  reference compositions, readiness, and audit evidence; deployment code owns
  real infrastructure, credentials, migrations execution, CI/CD, signing,
  publishing, deployment approval, and physical isolation.
- The public API audit is `uv run pytest tests/architecture/test_public_api.py -q`.
- The machine-readable public API inventory is
  `docs/public-api-inventory.json`; update it with any intentional public
  signature change. Every governed module must define `__all__`, and the
  inventory must exactly cover those exports so public API drift is visible in
  review.
