# Changelog

All notable AgentOS SDK changes for the current release line are summarized
here.

## Unreleased

### `0.3.0a1`: Phase 6 Distributed Runtime Cutover

- Added `DistributedRuntimeProfile` and `DistributedWorker` over the same async
  `Agent -> QueryLoop -> RunDriver` kernel used by Local and Durable profiles.
- Made PostgreSQL the only distributed truth for Run, accepted input,
  claim/fence, checkpoint, outbox, side effects, and Artifact metadata.
- Limited Redis to execution/relay delivery, leases, and bounded event replay;
  shared `BlobStore` implementations own Artifact bytes.
- Added stateless `ChannelServices` and `DistributedAsgiApp` composition for
  HTTP, SSE, WebSocket, and A2A.
- Removed the legacy distributed Session Snapshot path, synchronous
  PostgreSQL/Redis wrappers, old web runtime profiles, service-reference
  composition, and superseded channel/multi-agent modules without compatibility
  aliases or fallbacks.
- Added live failure-injection coverage for restart, timeout, claim/fence,
  checkpoint, outbox, artifact, side-effect, drain, and recovery behavior.
- Added the Phase 6 breaking map and upgraded release evidence to the canonical
  PostgreSQL/Redis/Worker backend set.

### Explicit Tool Side Effects

- Breaking: replaced `AsyncToolHandler` and bare argument handlers with one
  `ToolHandler(ToolInvocation)` contract; synchronous handlers are adapted only
  inside the asynchronous execution backend.
- Added explicit `SideEffectPolicy`, stable invocation/operation identity,
  typed compensation inputs, and inline/artifact Tool Result references as
  experimental `agentos.capabilities` APIs.

### Phase 5: Durable Runtime Profile

- Added stable module-level Durable command and profile APIs together with the
  SQLite Durable, Artifact, Plan, Memory, and Skill activation stores.
- Extended the stable `Agent`, `RunRequest`, and `WaitReason` signatures with
  additive Durable command and timer continuation inputs.
- Added the empty `agentos[durable]` installation extra; the reference Durable
  path remains standard-library only and does not require Redis or PostgreSQL.
- Kept all Durable adapters out of the root `agentos` facade and kept OCR out
  of the SDK scope.

### Phase 3C: Extension Projections

- Added request-bound Episodic/Semantic memory selection with explicit Session,
  access, expiry, score, deterministic Top-K, and complete-record projection.
- Added `MemoryRuntime` and `BoundMemoryProjectionProvider` as experimental
  `agentos.memory` APIs without wiring them into the Kernel or Builder.
- Clarified Skill activation as a version-pinned verified snapshot: synchronous
  instruction reads perform no hidden Source I/O, while the next explicit
  asynchronous load clears and revalidates the previous activation first.

### `0.2.0a1`: Single Async QueryLoop Cutover

- Breaking: replaced the sync/async dual-loop topology with one native async
  `QueryLoop`, one `ProviderAttemptRunner`, and one `await Agent.run(...)`
  execution entry.
- Removed the legacy async-prefixed Loop class, the duplicate Builder method,
  duplicate Agent run/stream methods, sticky interrupt clearing, and
  compatibility fallbacks.
- Added typed run inputs, WAITING outcomes, `AgentStream`, async SSE/JSONL
  serializers, stable execution errors, and the stable `agentos.sync` adapter.
- Added `docs/migrations/0.2-single-async-query-loop.md` for the required
  breaking migration.

### Phase 100: Release Hardening

- Added the release hardening gate and release candidate evidence checklist in
  `docs/release-hardening.md`.
- Added API stability classification in `docs/api-stability.md`.
- Added the migration index in `docs/migrations/README.md`.
- Aligned README, quickstart, production readiness, objective coverage audit,
  roadmap, and agent-os skill guidance with release governance.
- Kept CI/CD, signing, publishing, deployment approval, backend credentials,
  migration execution, and physical isolation deployment-owned.

### Phase 99: SDK Skill / Spec Generator Finalization

- Made `.claude/skills/agent-os` a production agent design constraint generator.
- Required production-bound specs to include `production_design_constraints`.
- Required explicit choices for agent form, runtime profile, state plane
  components, persistence backend, registry backend, queue backend, worker
  supervisor, A2A exposure, planner/team mode, production readiness checklist,
  and sandbox posture.

### Phase 98: Live Backend Probe Pack

- Added `ReferenceLiveBackendProbePack`, `ReferenceLiveBackendProbeSpec`,
  `REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME`, and
  `agentos.examples.live_backend_probe`.
- Added reference PostgreSQL state/Artifact, Redis queue/replay, and
  Distributed Worker probe invocation planning without creating backend
  clients.

### Phase 97: Reference State Plane Stack

- Added `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, and
  `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`.
- Composed the canonical PostgreSQL state/Artifact stores, Redis queue/replay
  adapters, Distributed Worker/Profile, Channel services/ASGI app, and readiness
  evidence without owning real infrastructure.

### Phase 96: Release Scope Re-baseline

- Defined the first production SDK release boundary in `docs/release-scope.md`.
- Reclassified Sandbox / Docker / E2B / microVM / enterprise runner adapter work
  as a non-blocking future adapter.
- Clarified that the release supports trusted tools, internal service
  orchestration, terminal agent, single-node web agent, distributed web agent,
  team/planner/A2A primitive composition, production state plane, readiness
  evidence, and audit evidence.
