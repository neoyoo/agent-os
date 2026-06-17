# Production State Plane Boundary Design

## Target Conclusion

Production AgentOS deployments need a clear state-plane split before more
adapters are added. AgentOS should expose a JSON-safe
`ProductionStatePlaneDeploymentProfile` that names the required production
planes and their responsibilities without implementing Nacos, Redis, Postgres,
Kubernetes, systemd, or sandbox infrastructure in this phase.

The target split is:

- `agent_registry`: discovery metadata such as Agent Cards, endpoints,
  capabilities, versions, and health metadata.
- `message_queue`: delivery, inbox, wakeup, and fan-out hints.
- `task_store`: task truth state, task results, retry state, and worker
  assignment evidence.
- `plan_store`: plan truth state, plan claims, scheduler recovery metadata, and
  planner execution evidence.
- `worker_process_supervisor`: local worker process lifecycle evidence such as
  start, running, stopped, exited, failed, and exit code.
- `session_snapshot_persistence`: context, messages, compression, working
  state, and session runtime snapshots.
- `state_plane_boundary_policy`: deployment documentation that prevents registry,
  queue, truth store, lifecycle, and session snapshot responsibilities from
  being mixed.
- `live_backend_verification`: deployment-owned verification that selected
  registry, queue, database, worker supervisor, and session persistence backends
  are reachable and correctly configured.

## Scope

This phase adds a readiness/profile boundary only. It does not add a Nacos
adapter, a worker supervisor implementation, a sandbox backend, or an agent
service reference app. Those are follow-on phases that should plug into this
state-plane split.

## SDK-Owned Boundary

AgentOS owns:

- The `ProductionStatePlaneDeploymentProfile` dataclass.
- Required component names.
- Missing component detection.
- JSON-safe readiness metadata and readiness check payloads.
- Documentation and SDK skill guidance that separates discovery, delivery,
  truth state, process lifecycle evidence, and runtime snapshots.

## Deployment-Owned Boundary

Deployments own:

- Nacos, static, or custom registry deployment and credentials.
- Redis or custom queue deployment and credentials.
- Postgres task, plan, and snapshot schema migration execution.
- Worker process supervisor implementation choice.
- Secret distribution, tenant directory integration, autoscaling, alert
  routing, runbooks, and live backend verification.

## Acceptance Criteria

- `ProductionStatePlaneDeploymentProfile` reports missing and configured
  components using the same readiness pattern as existing deployment profiles.
- Readiness metadata names the recommended future/backing roles:
  `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`,
  `PostgresPlanStore`, `WorkerProcessSupervisor`, and
  `SessionSnapshotPersistence`.
- Public imports expose the profile from `agentos.runtime` and top-level
  `agentos`.
- Readiness, production docs, objective coverage audit, roadmap, and
  `.claude/skills/agent-os` guidance describe the state-plane split.
- `QueryLoop` and `AsyncQueryLoop` remain unaware of production state-plane
  concepts.
