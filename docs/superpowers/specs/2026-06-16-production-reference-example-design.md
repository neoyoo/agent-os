# Production Reference Example Design

## Phase Goal

Phase 101: Production Reference Example should prove that the first production
SDK release can be assembled as a real reference shape, not only described as
protocols. The target composition is a production reference web agent using
`AgentServiceReference`, `DistributedWebRuntimeProfile`, a Nacos/Redis/Postgres
state plane, a readiness endpoint, backend verification evidence,
`ProductionReadinessEvidenceBundle`, and one planner primitive.

## Target Conclusion

Production Reference Example proves the first release target can be copied by
agent teams: `AgentServiceReference + DistributedWebRuntimeProfile +
Nacos/Redis/Postgres state plane + readiness endpoint + backend verification +
ProductionReadinessEvidenceBundle + planner primitive`. It remains an SDK
reference example. It does not create backend clients, real Nacos/Redis/Postgres
infrastructure, Kubernetes/systemd resources, CI/CD, credentials, migrations, or
sandbox isolation; those remain deployment-owned real infrastructure.

## SDK-Owned Boundary

- `AgentServiceReference` wires ASGI service hosting and readiness aggregation.
- `DistributedWebRuntimeProfile` demonstrates durable web session shape.
- `ReferenceStatePlaneStack` records component identity evidence.
- `ReferenceLiveBackendProbePack` records backend probe invocation plans.
- `DeploymentLiveBackendVerificationProfile` consumes backend evidence records.
- `ProductionReadinessEvidenceBundle` aggregates release gate evidence.
- `build_plan_and_execute_example()` demonstrates the planner primitive.

## Deployment-Owned Boundary

- Nacos, Redis, and Postgres deployment, credentials, network policy, migrations,
  and live probe execution.
- Worker process host policy, restart policy, autoscaling, and runbooks.
- Gateway, TLS, CORS, WAF, tenant directory, rollout, rollback, and alerting.
- Physical isolation for untrusted code execution and sandbox runtime patching.

## Acceptance

- A deterministic `agentos.examples.production_reference_web_agent` module exists.
- It has a main entrypoint and emits JSON evidence.
- It builds an `AgentServiceReference` and an ASGI `/ready` endpoint.
- It builds a `DistributedWebRuntimeProfile`.
- It builds a `ReferenceStatePlaneStack`.
- It includes identity evidence for `NacosAgentRegistryAdapter`,
  `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`,
  `PostgresSessionSnapshotPersistence`, and `LocalSubprocessWorkerSupervisor`.
- It includes `ReferenceLiveBackendProbePack`,
  `DeploymentLiveBackendVerificationProfile`, and
  `ProductionReadinessEvidenceBundle`.
- It includes a planner primitive, at minimum `PlannerRuntime` and
  `plan-and-execute`.
- The evidence is JSON-safe and contains no raw secret values.
- Release docs, objective audit, roadmap, and the agent-os skill guidance name
  the example and repeat the boundary-first posture.
