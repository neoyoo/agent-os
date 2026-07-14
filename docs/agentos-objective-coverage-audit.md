# AgentOS Objective Coverage Audit

Goal status: active

Previous completion estimate before Phase 97: 96%
Overall completion estimate: 96% before Phase 97.

Overall completion estimate: 97%
Overall completion estimate: 98%
Overall completion estimate: 99%
SDK-side checklist coverage: 100%
Production reference status: Phase 101 release-close evidence is now tracked
through the production reference web agent. The long-running goal can now be
audited against current implementation, docs, skill guidance, and verification
gates before it is marked complete.

This audit maps the original AgentOS SDK review objective to current evidence.
It is a planning ledger, not a completion certificate. Do not mark the long-running goal complete until every remaining production blocker has an implemented, tested, and documented SDK or deployment boundary.
It records non-certifying SDK evidence and is not a production certification.

Phase 96: Release Scope Re-baseline defines `docs/release-scope.md` as the
release scope re-baseline for the first production SDK release. The release
supports trusted tools, internal service orchestration, terminal agent,
single-node web agent, distributed web agent, team/planner/A2A primitive
composition, production state plane boundaries, readiness evidence, and audit
evidence. Sandbox / Docker / E2B / microVM / enterprise runner adapter work is
a non-blocking future adapter and not a release blocker. AgentOS does not
promise physical isolation for untrusted code execution; the SDK-owned surface
remains `WorkspaceExecutionBackend`, `SandboxBackend`,
`LocalWorkspaceExecutionBackend`, policy/capability/path pre-check, JSON-safe
execution evidence, and audit evidence. Production-bound specs must record the
sandbox posture as `trusted tools only`, `deployment-owned isolation`, or
`future adapter`.

Phase 97: Reference State Plane Stack adds `ReferenceStatePlaneStack`,
`ReferenceStatePlaneStackProfile`, and
`REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` as the reference state plane
composition layer. It connects `NacosAgentRegistryAdapter`,
`RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`,
`WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`,
`SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`,
`AgentServiceReference`, `DistributedWebRuntimeProfile`, and
`ProductionReadinessEvidenceBundle` through readiness source aggregation and
component identity evidence. It does not create backend clients; credentials,
migrations, CI matrix execution, alert routing and runbooks remain
deployment-owned.

Phase 98: Live Backend Probe Pack adds `ReferenceLiveBackendProbePack`,
`ReferenceLiveBackendProbeSpec`, `REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME`, and
`agentos.examples.live_backend_probe`. The probe pack declares a Nacos probe,
Redis probe, Postgres task/plan/session probe entries, and a worker supervisor
probe, generates argv-only `BackendVerificationInvocationPlan` values, and
turns `DeploymentLiveBackendVerificationRunResult` evidence into
`ProductionReadinessEvidenceBundle` through readiness bundle aggregation. It
does not create backend clients; credentials, migrations, CI matrix execution,
alert routing and runbooks remain deployment-owned.

Phase 99: SDK Skill / Spec Generator Finalization makes the agent-os skill a
spec generator finalization gate and a production agent design constraint
generator. Production-bound specs now need a `production_design_constraints`
SDK-owned constraint template that must explicitly choose agent form, runtime
profile, state plane components, persistence backend, registry backend, queue
backend, worker supervisor, A2A exposure, planner/team mode, production
readiness checklist, and sandbox posture: trusted tools only |
deployment-owned isolation | future adapter. The block records that AgentOS
does not create deployment-owned infrastructure.
Canonical sandbox posture phrase: sandbox posture: trusted tools only | deployment-owned isolation | future adapter.

Phase 100: Release Hardening changes the current work from adding runtime
features to producing release candidate evidence. The release hardening gate
requires a public API audit, stable API and experimental API classification,
migration index, README / quickstart / examples alignment, `CHANGELOG.md`,
full test suite evidence, diff/commit hygiene, and runtime boundary scans for
the single async `QueryLoop` and its `agentos.sync` adapter boundary. The canonical files are
`docs/release-hardening.md`, `docs/api-stability.md`,
`docs/migrations/README.md`, and `CHANGELOG.md`. This is SDK-owned release
evidence; AgentOS does not run CI/CD, signing, publishing, deployment approval.

Phase 101: Production Reference Example adds the production reference web
agent at `src/agentos/examples/production_reference_web_agent.py`, with tests
in `tests/examples/test_production_reference_web_agent.py`. It composes
`AgentServiceReference`, `DistributedWebRuntimeProfile`, a
Nacos/Redis/Postgres state plane, a readiness endpoint, backend verification,
`ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, and a planner primitive into one copyable SDK
reference. It does not create backend clients; Nacos, Redis, Postgres,
credentials, migrations, CI/CD, process supervision, live backend probe
execution, gateway/TLS, tenant directory integration, rollout, rollback,
alerting, runbooks, and sandbox isolation are deployment-owned real
infrastructure. Production readiness requires `state_plane_backend_targets`,
`reference_served_backend_binding`, and exact state-plane target_ref bindings
for every state-plane backend.

## Scoring

| Level | Meaning |
|-------|---------|
| Direct | The SDK has a stable path with tests and public guidance. |
| Primitives-ready | The SDK exposes usable primitives or profiles, but production policy remains deployment-owned. |
| Deployment-owned | The SDK intentionally defines a boundary and leaves the live operational mechanism to deployment code. |
| Future-extension | The SDK still needs a first-class boundary or implementation. |

## Coverage Matrix

| Original objective area | Current level | Evidence | Remaining production blockers |
|-------------------------|---------------|----------|--------------------------------|
| Terminal / Script Agents | Direct | `LocalRuntimeProfile`, `AgentBuilder`, sync `QueryLoop`, `WorkspaceExecutionIsolationProfile`, `WorkspaceExecutionBackend`, `LocalWorkspaceExecutionBackend`, `.claude/skills/agent-os/modules/agent-forms.md` | OS/container sandboxing for untrusted local tools remains deployment-owned. |
| Single-Node Async Web Agents | Direct | single async `QueryLoop`, `WebRuntimeProfile`, `AsgiAgentApp`, `AsyncAgentSessionProvider`, SSE/JSON turn endpoints | App-specific auth policy, workspace policy, and live backend verification remain deployment-owned. |
| Distributed Web Dynamic Context Hydration | Primitives-ready | `DistributedWebRuntimeProfile`, `DurableAgentSessionProvider`, `SessionLeaseStore`, `RedisSessionLeaseStore`, `SessionSnapshot`, `PostgresSessionSnapshotPersistence`, `DistributedWebSessionOperationsProfile`, `ProductionStatePlaneDeploymentProfile` | Credentials, migration execution, lease TTL tuning, stale lease recovery, auth and tenant integration, rollout and rollback, alerting, and live backend verification remain deployment-owned. |
| AgentScope2 / A2A Card, Registry, And Discovery | Primitives-ready | `A2AAgentCard`, `A2AAgentSkill`, `A2AAgentInterface`, `A2AAgentExtension`, `PersistentAgentRegistry`, `PostgresAgentRegistryStore`, `NacosAgentRegistryAdapter`, `NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, `NacosRegistryEvidence`, discovery-only Nacos metadata, AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering, capability-based discovery, `A2ACardResolver`, signed-card trust stores, OIDC/JWKS discovery primitives, `A2AExternalConformanceInvocationPlan`, `A2AExternalConformanceInvocationGateReport`, `A2AExternalConformanceRunner`, `A2AExternalConformanceCliRunner`, `A2AExternalConformanceExecutionProfile`, `A2AExternalConformanceExecutionRecord`, `A2AExternalConformanceGateReport`, external conformance invocation plan, external conformance invocation gate report, external conformance CLI runner, report path import, stdout JSON import, bounded stdout/stderr summaries, `env_keys`, no shell parsing, external conformance execution record, external conformance gate report | external conformance invocation planning, external suite invocation planning, SDK-owned reference CLI runner evidence, execution profile, SDK-owned execution record/gate report evidence, and a Nacos discovery adapter boundary are available, while official suite selection/installation, CI matrix execution, certification attestation, CA rollout, DNS pinning, enterprise egress proxy, tenant directory lifecycle, credential issuance and secret distribution, Nacos credentials, and live backend verification remain deployment-owned. |
| A2A Operation Interaction | Primitives-ready | `A2AOperationServer`, `A2AOperationClient`, `A2AOperationClient.stream_message`, `A2AOperationClient.stream_message_events`, `A2AMessageStreamEvent`, `parse_a2a_sse_events`, `A2AOperationClient.task_resubscribe`, `A2AStreamLifecycleDeploymentProfile`, `A2APushNotificationDeploymentProfile`, `A2AExternalConformanceInvocationPlan`, `A2AExternalConformanceInvocationGateReport`, `A2AExternalConformanceRunner`, `A2AExternalConformanceCliRunner`, `A2AExternalConformanceExecutionProfile`, `A2AExternalConformanceExecutionRecord`, `A2AExternalConformanceGateReport` | Automatic reconnect loops, durable cursor storage, fan-out, backpressure, gateway quota, billing, process supervision, external suite selection/installation, CI matrix execution, and certification attestation remain deployment-owned. |
| Multi-Agent Team Discussion | Primitives-ready | `DistributedTeamRuntimeProfile`, `ProductionStatePlaneDeploymentProfile`, `TeamRuntime`, `TeamTools`, `TeamWorkerRunner`, `TeamWorkerDaemon`, `WorkerProcessLifecycleDeploymentProfile`, `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, JSON-safe lifecycle evidence, argv-only, no shell parsing, `TeamWorkerPermissionPolicy`, `PostgresTeamStore`, `PostgresTeamUiStreamStore`, team UI JSON replay endpoint, team UI SSE/follow endpoint | Production process supervisor/job runner, restart policy, graceful drain, worker scaling execution, live backend verification implementation, migration execution, credentials, and OS/container sandboxing remain deployment-owned. |
| Single Async QueryLoop Guidance | Direct | single async `QueryLoop`, `await Agent.run(...)`, stable `agentos.sync.SyncAgent`, production guidance in `.claude/skills/agent-os/modules/agent-forms.md` | Kernel execution is always async; terminal/script hosts may adapt the same Agent through `agentos.sync`, while web and distributed workers await it directly. |
| Planner / Plan-And-Execute | Primitives-ready | `PlannerRuntime`, `PlannerTools`, `PlanState`, `PlanStep.depends_on`, `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`, `PlannerRuntime.gate_decomposition_proposal`, `plan_gate_decomposition_proposal`, `PlanDecompositionValidationReport`, `PlannerRuntime.validate_decomposition`, `PlannerDecompositionPolicyDeploymentProfile`, `PlannerLlmDecompositionGovernanceProfile`, governance reference readiness payloads, `component_refs`, `evidence_refs`, `budget_policy`, `PlannerLlmGovernanceEvidenceRecord`, `PlannerLlmGovernanceEvidenceGateReport`, `PlannerRuntime.gate_llm_governance_evidence`, planner LLM governance execution evidence, per-proposal governance evidence gate, `PlanRetryPolicy`, `plan_dispatch_ready_steps`, `PlannerRuntime.schedulable_plans`, `plan_schedulable_plans`, `PlannerSchedulablePlan`, `PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`, `PlanClaimRecord`, `PlannerRuntime.claim_schedulable_plans`, `plan_claim_schedulable_plans`, `PlannerRuntime.claimed_scheduler_tick`, `plan_claimed_scheduler_tick`, `PlanClaimedSchedulerTickReport`, `PlanClaimedSchedulerTickSkip`, `PlannerClaimedSchedulerDaemon`, `PlannerClaimedSchedulerDaemonState`, claimed scheduler daemon polling, `PlannerSchedulerGovernanceDeploymentProfile`, planner scheduler governance profile, `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `leader_election_policy`, `PlannerWorkerDispatchSupervisionProfile`, planner worker dispatch supervision profile, `PlanClaimSweepReport`, `PlanClaimSweepSkip`, `PlanClaimSweepStore`, `PlannerRuntime.sweep_expired_claims`, `PlannerStaleClaimSweepProfile`, planner stale claim sweep profile, stale claim sweep boundary, `stale_claim_sweep_schedule`, `sweep_safety_window`, `claimed_scheduler_tick_loop`, `metrics_alerting`, claim-before-tick scheduler boundary, `docs/migrations/2026-06-16-postgres-plan-claims.sql`, `plan_scheduler_tick`, `PlanSchedulerTickReport`, `PlannerSchedulerDaemon`, `PlannerSchedulerDaemonState`, `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, JSON-safe lifecycle evidence, argv-only, no shell parsing, `PostgresPlanStore`, `PlannerOrchestrationDeploymentProfile`, `WorkerProcessLifecycleDeploymentProfile`, `ProductionStatePlaneDeploymentProfile` | automatic LLM decomposition policy now has an SDK raw-proposal gate, validation/profile boundary, governance reference readiness payloads, a per-proposal governance evidence gate, scheduler polling has schedulable plan selection, local and Postgres-backed plan claim/lease boundaries, a claim-before-tick scheduler boundary, claimed scheduler daemon polling, scheduler governance readiness metadata, dispatch supervision payloads, exact stale-claim sweep reports/releases, a daemon over explicitly supplied plan ids, and a local worker lifecycle reference adapter, while deployment-owned prompt/model/approval/evaluation/validation execution, plan discovery tenant routing, global fairness, distributed scheduler locks, stale claim sweep scheduling policy, production process supervision, worker dispatch loop execution, compensation orchestration, credentials, migration execution, and live backend verification implementation remain deployment-owned. |
| Main-Agent Intent Routing With Subagent Execution | Primitives-ready | `SubAgentTemplate`, `PlanDecomposition`, `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`, `PlannerRuntime.gate_decomposition_proposal`, `plan_gate_decomposition_proposal`, `PlanDecompositionValidationReport`, `PlannerRuntime.validate_decomposition`, `PlannerDecompositionPolicyDeploymentProfile`, `PlannerLlmDecompositionGovernanceProfile`, governance reference readiness payloads, `PlannerLlmGovernanceEvidenceRecord`, `PlannerLlmGovernanceEvidenceGateReport`, `PlannerRuntime.gate_llm_governance_evidence`, per-proposal governance evidence gate, `AgentCoordinator`, `PlanDispatchReport`, `PlannerSchedulablePlan`, `PlanClaimStore`, `PostgresPlanClaimStore`, `PlanSchedulerTickReport`, `PlannerRuntime.claimed_scheduler_tick`, `PlanClaimedSchedulerTickReport`, `PlannerClaimedSchedulerDaemon`, `PlannerWorkerDispatchSupervisionProfile`, `PlannerRuntime.sweep_expired_claims`, `PlannerStaleClaimSweepProfile`, `PlannerSchedulerDaemon`, `WorkerProcessLifecycleDeploymentProfile`, `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `RemoteTaskExecutor`, `DistributedAgentProfile`, planner working-state projection | Intent classification prompt/policy, deployment-owned prompt/model/approval/evaluation/validation execution, retry compensation policy, tenant routing, global fairness, distributed scheduler locks, stale claim sweep scheduling policy, production process supervision, and production worker lifecycle execution remain app-owned. |
| Workspace Layer Expansion | Primitives-ready | `WorkspaceHandle`, `WorkspacePolicy`, `LocalWorkspaceProvider`, `WorkspaceToolSandboxPolicy`, `ToolPathSandboxRule`, team worker workspace narrowing, `WorkspaceExecutionIsolationProfile`, `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, `LocalWorkspaceExecutionBackend`, JSON-safe execution evidence, `env_keys`, Docker/E2B/enterprise runner adapter boundary | OS/container sandboxing, Docker/E2B/enterprise runner implementation, filesystem mount policy, network egress policy, CPU and memory limits, secret redaction, audit logging backend, sandbox image patching, and live sandbox verification remain deployment-owned. |
| SDK Developer Skill Guidance | Primitives-ready | `.claude/skills/agent-os/SKILL.md`, `.claude/skills/agent-os/modules/agent-forms.md`, `.claude/skills/agent-os/modules/architecture.md`, `.claude/skills/agent-os/modules/multi-agent.md`, `.claude/skills/agent-os/modules/persistence.md`, `docs/production-readiness.md`, `SkillReleaseManifest`, `SkillReleaseDriftReport`, `build_skill_release_manifest`, `compare_skill_release_manifests`, repository skill release manifest, installed user-level skill drift report | The SDK now exposes release manifest and drift report governance for version synchronization between the repository skill and installed user-level skill. install/copy/publish/sign approval remains deployment-owned, including overwrite prompts, marketplace entries, plugin cache busting, artifact signing, and release approval. |
| Production State Plane Boundary | Primitives-ready | `ProductionStatePlaneDeploymentProfile`, `production_state_plane`, `agent_registry`, `message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, `session_snapshot_persistence`, `state_plane_boundary_policy`, `NacosAgentRegistryAdapter`, `NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, `NacosRegistryEvidence`, discovery-only Nacos metadata, AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering, capability-based discovery, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, `WorkerProcessSpec`, `WorkerProcessState`, `LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`, `BackendVerificationRecord`, `DeploymentLiveBackendVerificationGateReport`, `DeploymentLiveBackendVerificationProfile`, `DeploymentLiveBackendVerificationRunResult`, `BackendVerificationInvocationPlan`, `BackendVerificationRunner`, `BackendVerificationCliRunner`, `BackendVerificationReportImporter`, `BackendVerificationReportImportError`, `LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS`, `LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS`, `deployment_live_backend_verification`, `block_production_readiness`, missing or failed backend evidence, invalid evidence, backend kind mismatch, reference runner, report path, stdout JSON, bounded stdout/stderr summaries, `env_keys`, no shell parsing, no backend client claim, registry is not task truth, queue is not final task or plan state, Nacos is not task truth, not plan truth, not session snapshot storage, not message queue, and not worker runtime state | Backend check script implementation, production supervisor integration, credentials and secret distribution, migrations, autoscaling, tenant directory integration, CI matrix execution, alert routing and runbooks remain deployment-owned. |
| Agent Service Reference Layer | Primitives-ready | `AgentServiceReference`, `AgentServiceReferenceProfile`, `AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS`, Agent Service Reference Layer, `AsgiAgentApp composition`, `DistributedWebRuntimeProfile injection`, `auth/rate-limit hook injection`, `readiness check aggregation`, JSON-safe readiness evidence, reference service, not a platform | gateway/TLS/CORS/WAF, tenant directory integration, Kubernetes/systemd/autoscaling, credentials, migrations, live backend verification, deployment rollout, and alerting remain deployment-owned. |
| Production Readiness Evidence Bundle Boundary | Primitives-ready | `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`, `ReadinessEvidenceStatus`, `blocking_checks`, `missing_required_checks`, `block_production_readiness`, release gate evidence bundle, consumes existing readiness/profile/backend evidence, does not execute real infrastructure checks, `sdk_owned`, `deployment_owned`, JSON-safe evidence bundle | The bundle consumes evidence only. Backend check execution, credentials, migrations, CI matrix execution, rollout, rollback, alerting, runbooks, release approval, and certification remain deployment-owned. |
| Release Scope Re-baseline | Direct | Phase 96: Release Scope Re-baseline, `docs/release-scope.md`, release scope re-baseline, first production SDK release, trusted tools, internal service orchestration, terminal agent, single-node web agent, distributed web agent, team/planner/A2A primitive, production state plane, readiness evidence, audit evidence, sandbox posture | Sandbox / Docker / E2B / microVM / enterprise runner adapter work is a non-blocking future adapter, not a release blocker, and does not promise physical isolation for untrusted code execution; production specs must choose `trusted tools only`, `deployment-owned isolation`, or `future adapter`. |
| Reference State Plane Stack | Primitives-ready | Phase 97: Reference State Plane Stack, `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`, reference state plane, readiness source aggregation, component identity evidence, `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`, `AgentServiceReference`, `DistributedWebRuntimeProfile`, `ProductionReadinessEvidenceBundle`, does not create backend clients | credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned. |
| Live Backend Probe Pack | Primitives-ready | Phase 98: Live Backend Probe Pack, `ReferenceLiveBackendProbePack`, `ReferenceLiveBackendProbeSpec`, `REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME`, `agentos.examples.live_backend_probe`, Nacos probe, Redis probe, Postgres task/plan/session probe, worker supervisor probe, readiness bundle aggregation, `BackendVerificationInvocationPlan`, `DeploymentLiveBackendVerificationRunResult`, `ProductionReadinessEvidenceBundle`, does not create backend clients | credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned. |
| SDK Skill / Spec Generator Finalization | Direct | Phase 99: SDK Skill / Spec Generator Finalization, spec generator finalization, production agent design constraint generator, `production_design_constraints`, must explicitly choose, agent form, runtime profile, state plane components, persistence backend, registry backend, queue backend, worker supervisor, A2A exposure, planner/team mode, production readiness checklist, sandbox posture: trusted tools only \| deployment-owned isolation \| future adapter, SDK-owned constraint template | The SDK does not create deployment-owned infrastructure; real infra creation, credentials, migrations, CI/CD, tenant directory, process supervision, backend probe execution, and physical isolation remain deployment-owned. |
| Release Hardening | Direct | Phase 100: Release Hardening, release hardening gate, release candidate evidence, public API audit, stable API, experimental API, API stability classification, migration index, README / quickstart / examples alignment, `CHANGELOG.md`, full test suite evidence, diff/commit hygiene, SDK-owned release evidence, `docs/release-hardening.md`, `docs/api-stability.md`, `docs/migrations/README.md` | AgentOS does not run CI/CD, signing, publishing, deployment approval; deployment-owned release operations still own pipeline execution, approval, artifact distribution, credentials, migrations execution, rollout, rollback, and physical isolation. |
| Production Reference Example | Direct | Phase 101: Production Reference Example, production reference web agent, `src/agentos/examples/production_reference_web_agent.py`, `tests/examples/test_production_reference_web_agent.py`, `AgentServiceReference`, `DistributedWebRuntimeProfile`, Nacos/Redis/Postgres state plane, readiness endpoint, backend verification, `ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`, `ReferenceLiveBackendProbePack`, planner primitive, does not create backend clients | Nacos, Redis, Postgres, credentials, migrations, CI/CD, process supervision, live backend probe execution, gateway/TLS, tenant directory integration, rollout, rollback, alerting, runbooks, and sandbox isolation are deployment-owned real infrastructure. |

## Remaining Priority Backlog

1. External A2A conformance suite governance: the SDK now exposes an
   `A2AExternalConformanceInvocationPlan` and
   `A2AExternalConformanceInvocationGateReport` for external suite invocation
   planning, an `A2AExternalConformanceRunner` protocol and
   `A2AExternalConformanceCliRunner` external conformance CLI runner that
   invokes commands with no shell parsing, imports a report path or stdout JSON,
   captures bounded stdout/stderr summaries, records `env_keys`, and redacts
   secret values, plus an external conformance execution profile plus
   `A2AExternalConformanceExecutionRecord` and
   `A2AExternalConformanceGateReport` for external conformance execution record
   and external conformance gate report evidence. Official suite
   selection/installation, environment matrix execution, release policy, live
   backend verification, and certification attestation remain deployment-owned.
2. LLM decomposition policy governance: the SDK now exposes
   `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`,
   `PlannerRuntime.gate_decomposition_proposal`,
   `plan_gate_decomposition_proposal`, `PlanDecompositionValidationReport`,
   `PlannerRuntime.validate_decomposition`, and
   `PlannerDecompositionPolicyDeploymentProfile`, plus
   `PlannerLlmDecompositionGovernanceProfile` for governance reference
   readiness payloads with `component_refs`, `evidence_refs`, and
   `budget_policy`, plus `PlannerLlmGovernanceEvidenceRecord`,
   `PlannerRuntime.gate_llm_governance_evidence`, and
   `PlannerLlmGovernanceEvidenceGateReport` as planner LLM governance execution
   evidence and a per-proposal governance evidence gate before production plan
   creation, but the actual automatic LLM decomposition and
   LLM prompt/model/approval/evaluation policy execution plus
   deployment-owned prompt/model/approval/evaluation/validation execution,
   rollout, and live backend verification remain deployment-owned.
3. Planner scheduler governance: the SDK now exposes schedulable plan selection
   through `PlannerRuntime.schedulable_plans`,
   `plan_schedulable_plans`, and `PlannerSchedulablePlan`, a local
   `PlanClaimStore` / `InMemoryPlanClaimStore` / `PostgresPlanClaimStore`
   plan claim/lease boundary through `PlannerRuntime.claim_schedulable_plans`,
   `plan_claim_schedulable_plans`, and
   `docs/migrations/2026-06-16-postgres-plan-claims.sql`, a
   claim-before-tick scheduler boundary through
   `PlannerRuntime.claimed_scheduler_tick`, `plan_claimed_scheduler_tick`,
   `PlanClaimedSchedulerTickReport`, and `PlanClaimedSchedulerTickSkip`, a
   `PlannerClaimedSchedulerDaemon` / `PlannerClaimedSchedulerDaemonState` for
   claimed scheduler daemon polling, a planner scheduler governance profile
   through
   `PlannerSchedulerGovernanceDeploymentProfile` with `plan_discovery_policy`,
   `tenant_routing_policy`, `global_fairness_policy`,
   `scheduler_lock_policy`, `leader_election_policy`,
   `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and
   `live_backend_verification` readiness components, a planner worker dispatch
   supervision profile through
   `PlannerWorkerDispatchSupervisionProfile` with
   `claimed_scheduler_tick_loop` and `metrics_alerting` readiness components,
   a stale claim sweep boundary through `PlanClaimSweepReport`,
   `PlanClaimSweepSkip`, `PlanClaimSweepStore`,
   `PlannerRuntime.sweep_expired_claims`, and
   `PlannerStaleClaimSweepProfile` with `stale_claim_sweep_schedule` and
   `sweep_safety_window` readiness components,
   plus a one-shot scheduler tick and
   `PlannerSchedulerDaemon` / `PlannerSchedulerDaemonState` for polling
   explicitly supplied plan ids. Plan discovery tenant routing, global fairness,
   distributed scheduler locks, stale claim sweep scheduling policy, process supervision, worker dispatch
   loop execution, and compensation orchestration remain
   deployment-owned.
4. worker process lifecycle execution: the SDK now exposes
   `WorkerProcessLifecycleDeploymentProfile`, `WorkerProcessSpec`,
   `WorkerProcessState`, `WorkerProcessSupervisor`, and
   `LocalSubprocessWorkerSupervisor`. The reference adapter gives local
   `team_worker`, `planner_worker`, and `a2a_push_worker` processes
   JSON-safe lifecycle evidence with `exit_code`, `started_at`,
   `stop_requested_at`, `stopped_at`, and `env_keys`; it is argv-only and uses
   no shell parsing. Production supervisor/job runner wiring, restart policy,
   graceful drain behavior, autoscaling, runbooks, credentials, migrations,
   live backend verification, Kubernetes, systemd, and secret distribution
   remain deployment-owned.
5. Production state plane adapters: the SDK now exposes
   `ProductionStatePlaneDeploymentProfile` for `production_state_plane`
   readiness across `agent_registry`, `message_queue`, `task_store`,
   `plan_store`, `worker_process_supervisor`,
   `session_snapshot_persistence`, `state_plane_boundary_policy`, and
   `live_backend_verification`. The target adapter boundaries are
   `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`,
   `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, and
   `SessionSnapshotPersistence`. Live backend evidence now has
   `BackendVerificationRecord`, `DeploymentLiveBackendVerificationGateReport`,
   `DeploymentLiveBackendVerificationProfile`, and the
   `BackendVerificationInvocationPlan` / `BackendVerificationRunner` /
   `BackendVerificationCliRunner` reference runner boundary. The runner imports
   a report path or stdout JSON, records bounded stdout/stderr summaries and
   `env_keys`, uses no shell parsing, redacts secret values, and emits a no
   backend client claim. The registry is not task truth, and the queue is not
   final task or plan state. Concrete backend check script implementation,
   production supervisor integration, credentials, migrations, autoscaling,
   tenant directory integration, alert routing, runbooks, release approval, and
   certification remain deployment-owned work.
6. Sandbox execution strategy: the SDK now exposes `WorkspaceExecutionBackend`,
   `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`,
   `WorkspaceExecutionPolicy`, and `LocalWorkspaceExecutionBackend`. The local
   reference adapter is argv-only, checks cwd containment, records JSON-safe
   execution evidence with `env_keys`, and is not a production isolation
   boundary. Docker/E2B/enterprise runner adapter implementation, container or
   microVM isolation, filesystem mount policy, network egress policy, resource
   enforcement, sandbox image patching, and live sandbox verification remain
   deployment-owned.
7. Skill release governance: the SDK now exposes `SkillReleaseManifest`,
   `SkillReleaseDriftReport`, `build_skill_release_manifest`, and
   `compare_skill_release_manifests` to compare the repository skill with an
   installed user-level skill through a release manifest and drift report.
   install/copy/publish/sign approval remains deployment-owned.
8. Agent Service Reference Layer: the SDK now exposes
   `AgentServiceReference`, `AgentServiceReferenceProfile`, and
   `AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS` as a reference service for
   production web agents. It uses AsgiAgentApp composition,
   DistributedWebRuntimeProfile injection, auth/rate-limit hook injection,
   readiness check aggregation, and JSON-safe readiness evidence without
   becoming a platform. gateway/TLS/CORS/WAF, tenant directory integration,
   Kubernetes/systemd/autoscaling, credentials, migrations, alerting, rollout,
   rollback, and live backend verification remain deployment-owned.
9. Production readiness evidence bundle: the SDK now exposes
   `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`, and
   `ReadinessEvidenceStatus` as a release gate evidence bundle. It consumes
   existing readiness/profile/backend evidence, reports `blocking_checks`,
   `missing_required_checks`, and `block_production_readiness`, emits
   `sdk_owned`, `deployment_owned`, and a JSON-safe evidence bundle, and does
   not execute real infrastructure checks. Backend check execution,
   credentials, migrations, CI matrix execution, rollout, rollback, alerting,
   runbooks, release approval, and certification remain deployment-owned.
10. Release scope convergence: Phase 96: Release Scope Re-baseline now records
   `docs/release-scope.md` as the release scope re-baseline for the first
   production SDK release. The supported release shapes are trusted tools,
   internal service orchestration, terminal agent, single-node web agent,
   distributed web agent, team/planner/A2A primitive composition, production
   state plane, readiness evidence, and audit evidence. Sandbox / Docker / E2B
   / microVM / enterprise runner adapter support is a non-blocking future
   adapter and not a release blocker. AgentOS does not promise physical
   isolation for untrusted code execution; current SDK ownership is
   `WorkspaceExecutionBackend`, `SandboxBackend`,
   `LocalWorkspaceExecutionBackend`, policy/capability/path pre-check, and
   audit evidence. Production specs must state sandbox posture as
   `trusted tools only`, `deployment-owned isolation`, or `future adapter`.
11. Reference state plane stack: Phase 97: Reference State Plane Stack now adds
   `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, and
   `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`. The stack composes
   `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`,
   `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`,
   `LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`,
   `PostgresSessionSnapshotPersistence`, `AgentServiceReference`,
   `DistributedWebRuntimeProfile`, and
   `ProductionReadinessEvidenceBundle` through readiness source aggregation and
   component identity evidence. It does not create backend clients;
   credentials, migrations, CI matrix execution, alert routing and runbooks
   remain deployment-owned.
12. Live backend probe pack: Phase 98: Live Backend Probe Pack now adds
   `ReferenceLiveBackendProbePack`, `ReferenceLiveBackendProbeSpec`,
   `REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME`, and
   `agentos.examples.live_backend_probe`. It declares a Nacos probe, Redis
   probe, Postgres task/plan/session probe entries, and a worker supervisor
   probe, emits argv-only `BackendVerificationInvocationPlan` metadata, and
   aggregates `DeploymentLiveBackendVerificationRunResult` evidence into
   `ProductionReadinessEvidenceBundle` through readiness bundle aggregation.
   It does not create backend clients; credentials, migrations, CI matrix
   execution, alert routing and runbooks remain deployment-owned.
13. SDK skill / spec generator finalization: Phase 99: SDK Skill / Spec
   Generator Finalization now makes `.claude/skills/agent-os` a spec generator
   finalization gate and production agent design constraint generator.
   Production-bound specs must include the `production_design_constraints`
   SDK-owned constraint template and must explicitly choose agent form, runtime
   profile, state plane components, persistence backend, registry backend,
   queue backend, worker supervisor, A2A exposure, planner/team mode,
   production readiness checklist, and sandbox posture: trusted tools only |
   deployment-owned isolation | future adapter. The template states that the
   SDK does not create deployment-owned infrastructure.
14. Release hardening: Phase 100: Release Hardening now defines the release
   hardening gate and release candidate evidence checklist. The branch must
   carry public API audit evidence, stable API and experimental API
   classification, a migration index, README / quickstart / examples
   alignment, `CHANGELOG.md`, full test suite evidence, diff/commit hygiene,
   and runtime boundary scan evidence. The canonical evidence files are
   `docs/release-hardening.md`, `docs/api-stability.md`, and
   `docs/migrations/README.md`. This is SDK-owned release evidence; AgentOS
   does not run CI/CD, signing, publishing, deployment approval.
15. Production reference example: Phase 101: Production Reference Example now
   completes the copyable first-release shape through the production reference
   web agent at `src/agentos/examples/production_reference_web_agent.py`, with
   tests in `tests/examples/test_production_reference_web_agent.py`. It
   composes `AgentServiceReference`, `DistributedWebRuntimeProfile`, a
   Nacos/Redis/Postgres state plane, a readiness endpoint, backend
   verification, `ProductionReadinessEvidenceBundle`,
   `ReferenceStatePlaneStack`, `ReferenceLiveBackendProbePack`, and a planner
   primitive. It does not create backend clients; deployment-owned real
   infrastructure remains outside the SDK. Phase 101 is complete enough for
   the long-running goal to enter requirement-by-requirement completion audit.

## Completion Interpretation

The SDK can already support the minimum requested terminal and web shapes. It
also has substantial A2A, registry/discovery, team discussion, planner, and
workspace primitives. The remaining work is mostly production orchestration and
governance rather than missing core runtime abstractions. Phase 96 narrows the
first production SDK release to trusted tools, internal service orchestration,
terminal agent, single-node web agent, distributed web agent,
team/planner/A2A primitive composition, production state plane, readiness
evidence, and audit evidence. Sandbox / Docker / E2B / microVM / enterprise
runner adapter support is a non-blocking future adapter, not a release blocker.
Phase 97 adds the reference state plane composition proof around registry,
queue, task and plan stores, worker supervisor, session snapshot persistence,
service reference, runtime profile, backend verification, and readiness
evidence boundaries.
Phase 98 adds the live backend probe pack reference invocation and example
report layer, so release gates can consume real deployment-owned probe output
without the SDK becoming a Nacos, Redis, Postgres, or supervisor client.
Phase 99 adds the `production_design_constraints` spec generator finalization
gate, so AgentOS now acts as a production agent design constraint generator
before implementation handoff.
Phase 100 adds release hardening gate evidence so the review branch can be
reviewed as an SDK release candidate. Phase 101 adds the production reference
web agent and ties `AgentServiceReference`, `DistributedWebRuntimeProfile`,
the Nacos/Redis/Postgres state plane, readiness endpoint, backend
verification, `ProductionReadinessEvidenceBundle`,
`ReferenceStatePlaneStack`, `ReferenceLiveBackendProbePack`, and a planner
primitive into a copyable release example.

SDK-side checklist coverage: 100% means the SDK-side first-release checklist
has implementation, docs, skill guidance, and reference example evidence ready
for completion audit. This is non-certifying SDK evidence, not a production
certification or deployment approval. Remaining items are deployment-owned
real infrastructure rather than SDK blockers: official external conformance
suite selection/installation, CI matrix execution, certification attestation,
deployment-owned prompt/model/approval/evaluation/validation execution, plan
discovery tenant routing, global fairness, distributed scheduler locks, stale
claim sweep scheduling policy, production process supervision, production
worker lifecycle execution, OS/container sandboxing, credential issuance and
secret distribution, live backend verification backend-specific probe
implementation, skill copying/publishing/signing/approval, and installing
skill releases.
