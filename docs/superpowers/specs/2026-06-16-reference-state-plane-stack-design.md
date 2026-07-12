# Reference State Plane Stack Design

## Target Conclusion

Phase 97: Reference State Plane Stack should not recreate Nacos, Redis,
Postgres, worker supervisors, or service hosting as a platform. It provides an
SDK-owned reference composition proving that registry, queue, task and plan
truth stores, worker lifecycle evidence, session snapshot persistence, service
reference, runtime profile, live backend verification, and readiness evidence
can be assembled into one auditable production state plane.

The stack is a composition and evidence boundary. It does not create backend
clients. Credentials, migrations, CI matrix execution, alert routing and
runbooks remain deployment-owned.

## SDK-Owned Boundary

- `ReferenceStatePlaneStack`
- `ReferenceStatePlaneStackProfile`
- `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`
- reference state plane metadata
- readiness source aggregation
- component identity evidence
- JSON-safe audit evidence
- `ProductionReadinessEvidenceBundle` aggregation

The stack accepts existing objects such as `NacosAgentRegistryAdapter`,
`RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`,
`WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`,
`SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`,
`AgentServiceReference`, and `DistributedWebRuntimeProfile`. It records their
identities and delegates readiness to existing `readiness_check`,
`readiness_metadata`, or `as_dict` methods when present.

## Deployment-Owned Boundary

Deployments own backend creation, Nacos/Redis/Postgres credentials, schema
migrations, CI matrix execution, live probe scripts, alert routing, runbooks,
tenant directory integration, autoscaling, rollout, rollback, and concrete
worker process host policy.

## Readiness Behavior

`ReferenceStatePlaneStackProfile` reports missing required components.
`ReferenceStatePlaneStack.build_readiness_bundle()` builds a
`ProductionReadinessEvidenceBundle` over the reference stack profile,
production state-plane profile, distributed web runtime profile, agent service
reference, and live backend verification evidence.

The stack blocks production readiness when required composition pieces are
missing or when a required downstream readiness source fails.
