# AgentOS API Stability

Canonical path: `docs/api-stability.md`.

## `0.2.0a1` Compatibility Target

The `0.2.0a1` root `agentos` facade commits only to the stable entry points
needed by the Level 1 local loop. ContextSnapshot, Artifact, Durable, and
Distributed types that are not implemented for that level are not exported in
advance. Later phases add public names only when their owning phase, behavior,
and compatibility tests are implemented.

Phase 2 applies a breaking message-boundary reset: `StoredMessage` is the only
business message truth type, while `ProviderInputItem` is the only accepted
`ProviderRequest.messages` value. The former `Message` alias and the legacy
Provider message DTO/serializer surface are removed without compatibility
facades. Provider tool schema values remain available from `agentos.providers`
and are owned by the dedicated `agentos.providers.tool_specs` module.

The machine-readable stability policy is `docs/public-api-stability.json`. It
contains only governed modules and the stable or experimental classification of
each export. The generated `docs/public-api-inventory.json` records the current
normalized `inspect.signature` baseline. Keeping policy and observed signatures
separate prevents a regenerated inventory from silently deciding compatibility.
Together they provide the API stability classification and machine-readable
public API inventory required by the public API audit. Release review treats
the two classifications explicitly as stable API and experimental API.

Regenerate the inventory with:

```text
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
```

## Governed Stable Namespaces

Every namespace listed here must be represented in
`docs/public-api-inventory.json`:

- `agentos`
- `agentos.channels`
- `agentos.multi`
- `agentos.persistence`
- `agentos.runtime`
- `agentos.sync`
- `agentos.workspace`
- `agentos.registry`
- `agentos.deployment`
- `agentos.readiness`
- `agentos.release`
- `agentos.testing`
- `agentos.testing.contracts`

## Stable API

Stable API should be safe for production specs to depend on with normal
deprecation and migration notes before incompatible change. The authoritative
stable surface is the set of `stable` exports in
`docs/public-api-inventory.json`; module descriptions below summarize those
governed exports and do not promote unlisted submodule names to stable API.

- root `AgentBuilder`, `Agent`, `QueryLoop`, and `ProviderRequestBuilder`
- stable `agentos.runtime` run contracts: `AgentResult`, `AgentWaiting`,
  `RunOutcome`, `RunRequest`, `RunInput`, `UserTurnInput`,
  `LocalContinuationInput`, `RunOptions`, `WaitReason`,
  `TurnStreamWaiting`, `AgentStream`, `iter_jsonl`, `iter_sse`, and the
  `AgentRunError` family
- stable `agentos.sync` adapter exports: `SyncAgent`, `SyncAgentStream`,
  `run`, and the `Sync*Error` types. These are not exported from root
  `agentos` and do not create a second QueryLoop.
- governed channel exports such as `AsgiAgentApp`, durable session provider
  protocols, session lease protocols, SSE replay/control stores, and
  `LeaseFencedSessionPersistence`
- governed persistence exports such as `SessionPersistence`,
  `SessionSnapshot`, `PostgresSessionSnapshotPersistence`, and related snapshot
  records/errors
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

## Testing Support API

The `agentos.testing` and `agentos.testing.contracts` namespaces are stable
adapter-author support APIs for reusable SDK contract tests. They are governed
by the public API inventory, but they are not exported from the root `agentos`
facade. Production application runtime code should depend on runtime
namespaces; adapter test suites should import contract runners from
`agentos.testing`.

## Compatibility Rules

- Stable API changes require a deprecation path or migration notes.
- Experimental API changes require changelog entries and test updates.
- Breaking migrations may happen only with the implementation phase that owns
  the change and a synchronized stability policy and generated inventory update.
- The root `agentos` namespace is a stable facade. Only names in
  `agentos.__all__` and the root inventory are supported as root imports.
  Stable or experimental names that are governed in submodules must be imported
  from their owning namespace, for example `agentos.channels`,
  `agentos.multi`, `agentos.runtime`, `agentos.sync`, `agentos.registry`, or
  `agentos.deployment`.
- boundary-first ownership stays unchanged: SDK API exposes protocols, profiles,
  reference compositions, readiness, and audit evidence; deployment code owns
  real infrastructure, credentials, migrations execution, CI/CD, signing,
  publishing, deployment approval, and physical isolation.
- The public API audit is `uv run pytest tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q`.
- Every governed module must define `__all__`. Update
  `docs/public-api-stability.json` for an intentional classification change,
  then regenerate `docs/public-api-inventory.json`; CI verifies the result on
  Python 3.11, 3.12, and 3.13 so public API drift remains visible in review.
