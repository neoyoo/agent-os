# Changelog

All notable AgentOS SDK changes for the current release line are summarized
here.

## Unreleased

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

### Phase 101: Production Reference Example

- Added the production reference web agent at
  `src/agentos/examples/production_reference_web_agent.py`.
- Composed `AgentServiceReference`, `DistributedWebRuntimeProfile`,
  Nacos/Redis/Postgres state plane evidence, readiness endpoint, backend
  verification, `ProductionReadinessEvidenceBundle`,
  `ReferenceStatePlaneStack`, `ReferenceLiveBackendProbePack`, and a planner
  primitive in one reference example.
- Kept backend clients, credentials, migrations, CI/CD, process supervision,
  and real infrastructure deployment-owned.

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
- Added reference Nacos, Redis, Postgres task/plan/session, and worker
  supervisor probe invocation planning without creating backend clients.

### Phase 97: Reference State Plane Stack

- Added `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, and
  `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`.
- Composed registry, queue, task store, plan store, worker supervisor, session
  snapshot persistence, service reference, runtime profile, and readiness
  evidence without owning real infrastructure.

### Phase 96: Release Scope Re-baseline

- Defined the first production SDK release boundary in `docs/release-scope.md`.
- Reclassified Sandbox / Docker / E2B / microVM / enterprise runner adapter work
  as a non-blocking future adapter.
- Clarified that the release supports trusted tools, internal service
  orchestration, terminal agent, single-node web agent, distributed web agent,
  team/planner/A2A primitive composition, production state plane, readiness
  evidence, and audit evidence.
