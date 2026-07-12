# Reference State Plane Stack Implementation Plan

## Phase 97 Target Conclusion

The SDK should expose a thin reference state plane composition that proves the
production backends and readiness evidence can be assembled without turning the
SDK into a platform. The implementation must keep real backend clients,
credentials, migrations, CI matrix execution, alert routing, and runbooks
deployment-owned.

## Tasks

1. Add failing tests for `ReferenceStatePlaneStack`,
   `ReferenceStatePlaneStackProfile`, JSON-safe readiness metadata, missing
   component blocking, and public API exports.
2. Implement `src/agentos/state_plane.py` with
   `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`,
   `ReferenceStatePlaneStackProfile`, and `ReferenceStatePlaneStack`.
3. Export the new API from top-level `agentos`.
4. Update production readiness, objective coverage audit, roadmap, and
   agent-os skill guidance so specs can select the reference state plane.
5. Verify targeted tests, docs tests, compileall, diff hygiene, runtime
   boundary scan, and the full test suite.

## Acceptance Criteria

- The stack records component identity evidence for
  `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`,
  `PostgresPlanStore`, `WorkerProcessSupervisor`,
  `SessionSnapshotPersistence`, `AgentServiceReference`, and
  `DistributedWebRuntimeProfile`.
- The stack builds a `ProductionReadinessEvidenceBundle`.
- Missing required components set `block_production_readiness`.
- The implementation does not create backend clients.
- `QueryLoop` and `AsyncQueryLoop` remain free of state-plane, worker, planner,
  team, A2A, readiness, and sandbox concepts.
