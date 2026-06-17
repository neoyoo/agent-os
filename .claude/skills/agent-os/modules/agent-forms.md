---
name: agent-os-agent-forms
description: What agent forms (archetypes) the SDK can build today; use to map requirements to modules and identify production gaps. Updated 2026-06-12.
---

# Agent Forms Coverage

Use this reference when a user describes what they want to build. Match the request to a form below, then state whether agent-os supports it directly, supports it with production glue, or still needs a future SDK extension.

For production planning, treat `agentos.readiness` as the structured source of truth. It breaks key forms down by session state, concurrency, auth, rate limiting, timeout, retry, observability, workspace, protocol, persistence, and schema migration. Do not call a form production-ready unless those dimensions are direct or explicitly handled by app glue.

For the public production checklist and form-by-form gaps, see
`docs/production-readiness.md`.

Phase 96: Release Scope Re-baseline defines `docs/release-scope.md` as the
release scope re-baseline for the first production SDK release. The release
supports trusted tools, internal service orchestration, terminal agent,
single-node web agent, distributed web agent, team/planner/A2A primitive
composition, production state plane boundaries, readiness evidence, and audit
evidence.

Sandbox / Docker / E2B / microVM / enterprise runner adapter work is a
non-blocking future adapter and not a release blocker. This SDK release does
not promise physical isolation for untrusted code execution. The SDK-owned
surface remains `WorkspaceExecutionBackend`, `SandboxBackend`,
`LocalWorkspaceExecutionBackend`, policy/capability/path pre-check, JSON-safe
execution evidence, and audit evidence. Every production-bound spec must name
its sandbox posture as `trusted tools only`, `deployment-owned isolation`, or
`future adapter`.

Production state-plane planning should use `ProductionStatePlaneDeploymentProfile`
and the `production_state_plane` readiness payload. Keep these components
separate:

- `agent_registry`: AgentCard, endpoint, capabilities, version, and health
  metadata discovery through `NacosAgentRegistryAdapter` or a custom registry.
  The registry is not task truth.
- `message_queue`: worker messages, inbox, wakeup, delivery, and fan-out hints
  through `RedisAgentMessageQueue`. The queue is not final task or plan state.
- `task_store`: task truth, result, retry, and assignment evidence through
  `PostgresTaskStore`.
- `plan_store`: plan truth, claims, scheduler recovery, and planner evidence
  through `PostgresPlanStore`.
- `worker_process_supervisor`: worker start/running/stop/exit/fail evidence
  through `WorkerProcessSupervisor`; use `LocalSubprocessWorkerSupervisor` as
  the argv-only local reference adapter when SDK-owned JSON-safe lifecycle
  evidence is enough.
- `session_snapshot_persistence`: context, messages, compression, working
  state, and session runtime snapshots through `SessionSnapshotPersistence`.
- `state_plane_boundary_policy`: deployment policy preventing registry, queue,
  truth stores, lifecycle state, and session snapshots from being mixed.

Live backend verification evidence should use `BackendVerificationRecord`,
`DeploymentLiveBackendVerificationGateReport`,
`DeploymentLiveBackendVerificationProfile`, and
`LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS`. The
`deployment_live_backend_verification` probe covers `agent_registry`,
`message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, and
`session_snapshot_persistence`; it sets `block_production_readiness` on
missing or failed backend evidence. AgentOS consumes JSON-safe evidence only.
Deployment owns backend check execution, credentials and secret distribution,
CI matrix execution, alert routing and runbooks.

When a production spec needs a repeatable evidence collection path, use
`BackendVerificationInvocationPlan`, `BackendVerificationRunner`,
`BackendVerificationCliRunner`, `BackendVerificationReportImporter`,
`BackendVerificationReportImportError`, and
`DeploymentLiveBackendVerificationRunResult`. The reference runner is
argv-only, uses no shell parsing, imports a report path or stdout JSON, records
bounded stdout/stderr summaries and `env_keys`, redacts configured secret
values, emits a no backend client claim, and is not a live backend client.
Deployments still own the real Nacos/Redis/Postgres/supervisor/session checks,
credentials, migrations, CI matrix execution, alert routing, runbooks, release
approval, and certification.

For release gating, use `ProductionReadinessEvidenceBundle`,
`ReadinessEvidenceCheck`, and `ReadinessEvidenceStatus` to create a release
gate evidence bundle that consumes existing readiness/profile/backend evidence.
It reports `accepted`, `blocking_checks`, `missing_required_checks`, and
`block_production_readiness`, includes `sdk_owned` and `deployment_owned`
boundary metadata, emits a JSON-safe evidence bundle, and does not execute real
infrastructure checks. Deployment still owns backend checks, credentials,
migrations, CI matrix execution, rollout, rollback, alerting, runbooks, and
release approval.

Phase 97: Reference State Plane Stack adds `ReferenceStatePlaneStack`,
`ReferenceStatePlaneStackProfile`, and
`REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` as the reference state plane for
standard production compositions. Use it to connect
`NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`,
`PostgresPlanStore`, `WorkerProcessSupervisor`,
`LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`,
`PostgresSessionSnapshotPersistence`, `AgentServiceReference`,
`DistributedWebRuntimeProfile`, and `ProductionReadinessEvidenceBundle` through
readiness source aggregation and component identity evidence. It does not
create backend clients; credentials, migrations, CI matrix execution, alert
routing and runbooks remain deployment-owned.

Nacos registry adapter boundary: use `NacosAgentRegistryAdapter`,
`NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, and
`NacosRegistryEvidence` when Nacos backs AgentCard discovery. The SDK-owned
boundary is AgentCard-to-Nacos metadata projection, healthy Nacos instances
filtering, capability-based discovery, and JSON-safe evidence. This is
discovery-only Nacos metadata: it is not task truth, not plan truth,
not session snapshot storage, not message queue, and not worker runtime state.
Deployments own credentials, concrete client construction, TLS/auth, namespace
and tenant policy, service naming, and live backend verification.

Worker process lifecycle execution can use `WorkerProcessSpec`,
`WorkerProcessState`, `WorkerProcessSupervisor`, and
`LocalSubprocessWorkerSupervisor` for local reference supervision. The evidence
shape covers `team_worker`, `planner_worker`, and `a2a_push_worker` processes,
including `exit_code`, `started_at`, `stop_requested_at`, `stopped_at`, and
`env_keys`. The adapter is argv-only, uses no shell parsing, and is not a Kubernetes, systemd, autoscaling, or secret-distribution layer.

## Readiness Terms

| Term | Meaning |
|------|---------|
| Direct | The SDK has a stable builder/runtime path and tests for this form. A user can build it without defining new SDK-level infrastructure. |
| Primitives ready | The SDK has the core modules, adapters, and tests, but the user must write orchestration glue or production policy. |
| Future extension | The SDK does not yet expose a first-class boundary for this form. Treat it as roadmap work, not a quick configuration choice. |

## Directly Supported

### 1. Single-Turn Conversational Agent
**Readiness**: Direct
**Modules**: `Agent.run()` + `Provider` + `ContextRenderer`
**Typical use**: customer support Q&A, content generation, translation, summarization

### 2. Tool-Calling Agent
**Readiness**: Direct
**Modules**: `ToolRegistry` + `ToolCallRouter` + `SecurityPolicy` + `WorkspaceToolSandboxPolicy` + `RegisteredTool`
**Typical use**: code execution, API orchestration, data queries, file manipulation

### 3. MCP-Integrated Agent
**Readiness**: Direct for registered MCP tools; production MCP server lifecycle remains app-owned
**Modules**: `MCPRegistry` + `MCPClient` Protocol + `MCPToolAdapter`

### 4. Streaming Agent
**Readiness**: Direct
**Modules**: `Agent.stream()` / `stream_sse()` / `stream_jsonl()` / `async_stream()`
**Typical use**: real-time chat UI, SSE push, JSONL over HTTP

### 5. Long-Conversation Agent
**Readiness**: Direct
**Modules**: `CompressionRuntime` + `BudgetPolicy` + `RecallRuntime` + `CompressionIndex`
**Typical use**: multi-turn deep dialogue, research assistants, long-running workflows

### 6. Async Agent
**Readiness**: Direct
**Modules**: `AgentBuilder.build_async()` + `Agent.async_run()` / `async_stream()` + async provider/tool paths
**Typical use**: FastAPI, aiohttp, Starlette, or any host that already has an event loop
**Boundary**: Sync-only adapters use executor fallback; do not force async for simple terminal/script agents.

### 7. Local HTTP Agent
**Readiness**: Direct for single-process HTTP
**Modules**: `AsgiAgentApp` + `InMemoryAgentSessionProvider` + `ChannelAuthPolicy` + `RateLimiter`
**Boundary**: `InMemoryAgentSessionProvider` keeps agent state in the process.

### 8. Session-Snapshot Agent
**Readiness**: Direct persistence primitives; app-owned restore/save lifecycle
**Modules**: `SessionSnapshot` + `SessionPersistence` (`MemoryPersistence`, `SQLitePersistence`, `FileSystemPersistence`) + serializers
**Boundary**: Snapshot persistence serializes session state. Production web hydration should use `DurableAgentSessionProvider`.

### 9. Local Multi-Agent Coordination
**Readiness**: Direct for local coordination
**Modules**: `AgentCoordinator` + `TaskTable` + `AgentInbox` + `SpawnExecutor`
**Modes**: `spawn` for ephemeral subagents, `dispatch` for persistent experts

### 10. Observable Agent
**Readiness**: Direct
**Modules**: `ObservabilityConfig` + OpenTelemetry tracer + Langfuse + `EventLog` + snapshots

### 11. Interruptible Agent
**Readiness**: Direct
**Modules**: `Agent.interrupt()` + `QueryLoop` interrupt checkpoints

### 12. Hook-Guarded Agent
**Readiness**: Direct
**Modules**: `HookManager` + `HookHandler` Protocol + lifecycle hook points
**Actions**: `allow` / `deny` / `modify`

### 13. Callback Agent
**Readiness**: Direct
**Modules**: `Agent.run_with_callbacks()` + typed callback signatures

### 14. Continuation Agent
**Readiness**: Direct primitive; distributed wakeup requires adapter wiring
**Modules**: `ContinuationTrigger` + `Agent.run_continuation()` + `TurnNoticeProvider`

## Primitives Ready, Production Glue Required

### 15. Web Distributed Agent
**Available**: `DistributedWebRuntimeProfile`, `DistributedWebSessionOperationsProfile`, `ProductionStatePlaneDeploymentProfile`, `WebRuntimeProfile`, `AsgiAgentApp`, JSON/SSE turn endpoints, resumable SSE buffer boundary, `AsyncAgentSessionProvider`, `DurableAgentSessionProvider`, `SessionLeaseStore`, `RedisSessionLeaseStore`, `SnapshotAgentFactory`, `SessionSnapshot`, `PostgresSessionSnapshotPersistence`, Redis/Postgres memory primitives
**Missing**: app-specific workspace policy, Redis TTL/stale-turn policy, Postgres migration execution, operational lock timeout and recovery settings
**Use**: `DistributedWebRuntimeProfile` to assemble `DurableAgentSessionProvider` with `RedisSessionLeaseStore` and `PostgresSessionSnapshotPersistence`, then add deployment-owned auth, workspace, migration, and recovery policy. Use `DistributedWebSessionOperationsProfile` to expose readiness metadata for `durable_session_provider`, `lease_store`, `snapshot_persistence`, `snapshot_migration`, `lease_ttl_policy`, `stale_lease_recovery`, `credential_policy`, `auth_tenant_policy`, `workspace_policy`, and `live_backend_verification`; use `ProductionStatePlaneDeploymentProfile` to show the separate `agent_registry`, `message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, `session_snapshot_persistence`, `state_plane_boundary_policy`, and `live_backend_verification` assignments. SDK-owned protections stop at `DurableAgentSessionProvider`, `RedisSessionLeaseStore`, `PostgresSessionSnapshotPersistence`, and the acquire/hydrate/save/release lifecycle, while Redis/Postgres credentials, migration execution, lease TTL tuning, stale lease recovery policy, auth and tenant integration, and live backend verification remain deployment-owned.
**Use when**: any node may receive any turn for the same session.

### 16. Distributed Multi-Agent Task Agent
**Available**: `PostgresTaskStore`, `RedisAgentMessageQueue`, `OutboxReconciler`, `RedisContinuationTrigger`, `RemoteTaskExecutor`, `A2AAdapter`
**Missing**: production profile wiring, cancellation scheduling, OS/container sandboxing for untrusted worker code
**Workaround**: wire `AgentCoordinator` with Postgres/Redis adapters and provide worker process lifecycle.

### 17. Team Discussion Agent
**Available**: `DistributedTeamRuntimeProfile`, `ProductionStatePlaneDeploymentProfile`, `TeamRuntime`, `TeamTools`, `TeamWorkerSessionProvider`, `InMemoryTeamWorkerSessionProvider`, `TeamWorkerPermissionPolicy`, `TeamWorkerRunner`, `TeamWorkerDaemon`, `WorkerProcessLifecycleDeploymentProfile`, `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `TeamWorkerRetryPolicy`, `InMemoryTeamWorkerRetryStore`, `PostgresTeamWorkerRetryStore`, `TeamWorkerCancellationStore`, `InMemoryTeamWorkerCancellationStore`, `PostgresTeamWorkerCancellationStore`, `TeamUiEvent`, `InMemoryTeamUiStreamStore`, `PostgresTeamUiStreamStore`, team UI JSON replay endpoint, team UI SSE/follow endpoint, `TeamRecord`, `TeamMemberRecord`, `TeamMessage`, `InMemoryTeamStore`, `PostgresTeamStore`, `TeamNoticeStore`, `AgentMessageQueue` wakeup hints, workspace handles on teams/members, `WorkspaceExecutionIsolationProfile`, `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, `LocalWorkspaceExecutionBackend`
**Missing**: Redis/Postgres credential wiring, migration execution, actual process supervisor/job runner implementation, worker scaling policy execution, live backend verification implementation, OS/container sandboxing for untrusted worker code
**Workaround**: use `DistributedTeamRuntimeProfile` as the default preset to assemble `TeamRuntime`, `TeamWorkerRunner`, `TeamWorkerDaemon`, worker session provider, retry/cancellation stores, message queue, and UI stream into one coherent boundary. Use `TeamTools` for LLM-callable team create/member/message/read/delete operations, use `TeamWorkerSessionProvider` to register independent worker sessions, use `TeamWorkerPermissionPolicy` to reject worker workspaces that broaden scope or escape the team root and to enforce a capability allow-list, attach `WorkspaceToolSandboxPolicy` to worker `ToolCallRouter` instances for path/capability pre-execution checks, use `WorkspaceExecutionIsolationProfile` to expose readiness metadata for `workspace_policy`, `tool_path_sandbox`, `capability_allowlist`, `execution_backend`, `process_isolation`, `resource_limits`, `network_policy`, and `audit_logging` while keeping OS/container sandboxing, filesystem mount policy, network egress policy, CPU and memory limits, secret redaction, audit logging backend, and live sandbox backend verification deployment-owned, use `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, and `WorkspaceExecutionPolicy` when a team worker needs a pluggable Local/Docker/E2B/enterprise runner boundary, use `LocalWorkspaceExecutionBackend` as the argv-only local reference backend that emits JSON-safe execution evidence with `env_keys`, and state that it is not a production isolation boundary while Docker/E2B/enterprise runner adapters remain deployment-owned, use `WorkerProcessLifecycleDeploymentProfile` to expose `worker_process_lifecycle` readiness metadata for `process_supervisor`, `restart_policy`, `graceful_shutdown`, `health_probe`, `readiness_probe`, `scaling_policy`, `credential_policy`, `migration_policy`, `alerting`, and `live_backend_verification`, and use `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and `LocalSubprocessWorkerSupervisor` when a local team worker service needs argv-only start/stop/wait evidence with JSON-safe lifecycle evidence including `exit_code`, `started_at`, `stop_requested_at`, `stopped_at`, and `env_keys`; the reference adapter uses no shell parsing and is not a Kubernetes, systemd, autoscaling, or secret-distribution layer, while keeping the production process supervisor or job runner, restart policy, graceful shutdown and draining, horizontal scaling policy, credentials and secret distribution, schema migration execution, live backend verification, health/readiness endpoint wiring, alert routing and runbooks, and OS/container sandboxing deployment-owned. Use `PostgresTeamStore` for multi-node team state, use `TeamWorkerRunner` with `TeamWorkerRetryPolicy` to process team-message continuation batches without hot-looping failed deliveries, use `PostgresTeamWorkerRetryStore` when retry state must survive restarts or cross nodes, use `TeamWorkerCancellationStore` to skip queued or retry-delayed worker continuations before execution, use `PostgresTeamWorkerCancellationStore` when cancellation intent must survive restarts or cross nodes, use `InMemoryTeamUiStreamStore` for local UI projection, use `PostgresTeamUiStreamStore` plus `GET /v1/teams/{team_id}/ui-events` and `GET /v1/teams/{team_id}/ui-events/stream` for multi-node UI replay/follow, and run `TeamWorkerDaemon` from a supervised service process.

### 18. Planner / Intent-Router Agent
**Available**: `PlannerRuntime`, `PlannerTools`, `ProductionStatePlaneDeploymentProfile`, `PlanState`, `PlanStep`, `PlanStep.depends_on`, `PlanRetryPolicy`, failure/retry metadata (`attempts`, `last_failed_at`, `next_retry_at`, `retry_status`), `PlanDispatchReport`, `PlanDispatchSkip`, `PlanSchedulerRetryReset`, `PlanSchedulerTickReport`, `PlanClaimedSchedulerTickReport`, `PlanClaimedSchedulerTickSkip`, `PlannerClaimedSchedulerDaemon`, `PlannerClaimedSchedulerDaemonState`, `PlannerClaimedSchedulerDaemonError`, claimed scheduler daemon polling, `PlannerSchedulerGovernanceDeploymentProfile`, planner scheduler governance profile, `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `leader_election_policy`, `PlannerWorkerDispatchSupervisionProfile`, `PlanClaimSweepReport`, `PlanClaimSweepSkip`, `PlanClaimSweepStore`, `PlannerRuntime.sweep_expired_claims`, `PlannerStaleClaimSweepProfile`, `PlannerSchedulablePlan`, `PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`, `PlanClaimRecord`, `PlannerRuntime.claim_schedulable_plans`, `PlannerRuntime.claimed_scheduler_tick`, `PlannerSchedulerDaemon`, `PlannerSchedulerDaemonState`, `PlanDecomposition`, `PlanStepSpec`, `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`, `PlannerRuntime.gate_decomposition_proposal`, `PlanDecompositionValidationReport`, `PlannerRuntime.validate_decomposition`, `PlannerRuntime.schedulable_plans`, `PlannerDecompositionPolicyDeploymentProfile`, `PlannerLlmDecompositionGovernanceProfile`, governance reference readiness payloads, `component_refs`, `evidence_refs`, `budget_policy`, `PlannerLlmGovernanceEvidenceRecord`, `PlannerLlmGovernanceEvidenceGateReport`, `PlannerRuntime.gate_llm_governance_evidence`, planner LLM governance execution evidence, per-proposal governance evidence gate, `SubAgentTemplate`, `EvidenceHandle`, `InMemoryPlanStore`, `PostgresPlanStore`, `docs/migrations/2026-06-16-postgres-plan-claims.sql`, `PlannerOrchestrationDeploymentProfile`, `WorkerProcessLifecycleDeploymentProfile`, `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `WorkspaceExecutionIsolationProfile`, `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, `LocalWorkspaceExecutionBackend`, `AgentCoordinator` assignment boundary, `plan_gate_decomposition_proposal`, `plan_create_from_decomposition`, `plan_ready_steps`, `plan_schedulable_plans`, `plan_claim_schedulable_plans`, `plan_claimed_scheduler_tick`, `plan_dispatch_ready_steps`, `plan_scheduler_tick`, `plan_fail_step`, `plan_retryable_steps`, `plan_retry_step`, `plan_to_working_state_summary`
**Missing**: automatic LLM decomposition policy, LLM prompt/model/approval/evaluation policy, deployment-owned prompt/model/approval/evaluation/validation execution, real tenant routing implementation, global fairness queues, distributed scheduler locks, stale claim sweep scheduling policy, process supervision, worker dispatch loop execution, worker process lifecycle management, live backend verification implementation, complex compensation policy
**Workaround**: use `PlanStore` as the truth source, use `PostgresPlanStore` when plan state must survive restarts or cross nodes, use `PlannerRuntime.gate_decomposition_proposal(...)` or `plan_gate_decomposition_proposal` to parse, normalize, policy-check, approval-gate, and audit a raw LLM/main-agent JSON decomposition proposal before it can be persisted, use `PlannerRuntime.validate_decomposition(...)` to get a JSON-safe `PlanDecompositionValidationReport` before an LLM-produced `PlanDecomposition` is persisted, use `PlannerLlmGovernanceEvidenceRecord`, `PlannerRuntime.gate_llm_governance_evidence`, and `PlannerLlmGovernanceEvidenceGateReport` as the per-proposal governance evidence gate for planner LLM governance execution evidence before production plan creation; the gate blocks missing or failed prompt/model/approval/evaluation/validation evidence while deployment-owned prompt/model/approval/evaluation/validation execution still owns the actual prompt run, model router, approval workflow, evaluation suite, schema validation, artifact retention, and certification decision, use `PlanRetryPolicy` plus `plan_fail_step`/`plan_retryable_steps`/`plan_retry_step` for auditable failed-step recovery, use `plan_dispatch_ready_steps` for bounded ready-step submission through `AgentCoordinator`, use `PlannerRuntime.schedulable_plans(...)` or `plan_schedulable_plans` for owner-scoped, status-filtered, JSON-safe schedulable plan selection that returns `PlannerSchedulablePlan` summaries for plans with ready pending steps or due retryable failed steps, use `PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`, `PlannerRuntime.claim_schedulable_plans(...)`, or `plan_claim_schedulable_plans` for an SDK-owned plan claim/lease boundary that returns JSON-safe `PlanClaimRecord` values and `claimed`/`busy` results, use `PlannerRuntime.claimed_scheduler_tick(...)` or `plan_claimed_scheduler_tick` for a claim-before-tick scheduler boundary that only ticks plans claimed by the current worker and returns `PlanClaimedSchedulerTickReport` / `PlanClaimedSchedulerTickSkip`, use `PlannerClaimedSchedulerDaemon` for claimed scheduler daemon polling over `PlannerRuntime.claimed_scheduler_tick(...)` with `PlannerClaimedSchedulerDaemonState` and `PlannerClaimedSchedulerDaemonError`, use `PlannerSchedulerGovernanceDeploymentProfile` as a planner scheduler governance profile for `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `scheduler_lock_policy`, `leader_election_policy`, `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and `live_backend_verification` scheduler governance readiness metadata, use `PlannerWorkerDispatchSupervisionProfile` as a planner worker dispatch supervision profile over recent reports to emit a dispatch supervision payload with `claimed_scheduler_tick_loop`, `metrics_alerting`, claim/busy/tick-failed/release counts, and consecutive-failure readiness metadata, use `PlannerRuntime.sweep_expired_claims(...)` for a stale claim sweep boundary that returns `PlanClaimSweepReport`, supports dry-run, and releases only exact expired `PlanClaimRecord` values through `PlanClaimSweepStore`, use `PlannerStaleClaimSweepProfile` as a planner stale claim sweep profile with `stale_claim_sweep_schedule`, `sweep_safety_window`, `plan_claim_store`, `scheduler_lock_policy`, `metrics_alerting`, and `live_backend_verification` readiness metadata, use `docs/migrations/2026-06-16-postgres-plan-claims.sql` when Postgres-backed claim state must survive restarts or coordinate scheduler workers across nodes, use `plan_scheduler_tick` or `PlannerRuntime.scheduler_tick(...)` when a deployment-owned daemon, cron job, or scheduler agent needs one SDK-owned pass that resets due retryable steps before bounded ready-step dispatch, use `PlannerSchedulerDaemon` when a supervised service process needs a small SDK-owned polling loop around `PlannerRuntime.scheduler_tick(...)` for explicitly supplied plan ids and `PlannerSchedulerDaemonState` status/reports/errors, use `PlannerDecompositionPolicyDeploymentProfile` to expose readiness metadata for `prompt_policy`, `output_schema`, `validation_gate`, `template_mapping_policy`, `approval_policy`, `model_routing_policy`, `evaluation_policy`, `trace_logging`, and `rollback_policy` while keeping prompt/model/approval/eval and rollout policy deployment-owned, use `PlannerLlmDecompositionGovernanceProfile` to expose governance reference readiness payloads with `component_refs`, `evidence_refs`, and `budget_policy` for app-owned prompt/model/approval/evaluation/trace/rollback/schema/validation/template/budget/live-verification controls while keeping prompt text and prompt review workflow deployment-owned, use `PlannerOrchestrationDeploymentProfile` to expose readiness metadata for `decomposition_policy`, `dag_scheduler`, `worker_dispatch_loop`, `compensation_policy`, `plan_store`, and `worker_supervision`, use `WorkerProcessLifecycleDeploymentProfile` when planner worker or scheduler services need `worker_process_lifecycle` readiness metadata for `process_supervisor`, `restart_policy`, `graceful_shutdown`, `health_probe`, `readiness_probe`, `scaling_policy`, `credential_policy`, `migration_policy`, `alerting`, and `live_backend_verification`, keep plan discovery sources, distributed scheduler locks, stale claim sweep scheduling policy, process supervision, credentials, migrations, worker dispatch loop execution, and compensation orchestration deployment-owned, use `WorkspaceExecutionIsolationProfile` when generated subagent work needs an execution isolation readiness contract, and write only `plan_to_working_state_summary(plan)` output into working state when a prompt needs plan awareness.

### 19. Agent Registry / Discovery Agent
**Available**: internal `AgentCard`, `PersistentAgentRegistry`, `PostgresAgentRegistryStore`, `StaticResolver`, `ServiceResolver`, session affinity, plus A2A protocol card serialization, skill-level input/output media metadata, `A2AAgentInterface`/`supportedInterfaces`, `A2AAgentExtension` capability extensions, well-known publication/discovery primitives, protocol version negotiation via `A2AProtocolVersionPolicy` and `A2A-Version`, extension negotiation via `A2AExtensionNegotiationPolicy` and `A2A-Extensions`, A2A 1.0 text/file/data part payload shapes, `A2AArtifact`, `statusUpdate`/`artifactUpdate` wrapper serializers, SDK self-conformance reports via `A2AConformanceHarness` including `message/stream request`, typed `message stream event`, `tasks/resubscribe request`, and task resubscribe statusUpdate event checks, external conformance result import via `A2AExternalConformanceReportImporter`, external conformance invocation planning via `A2AExternalConformanceInvocationPlan` and `A2AExternalConformanceInvocationGateReport`, external conformance CLI runner reference execution via `A2AExternalConformanceRunner` and `A2AExternalConformanceCliRunner` with argv-only commands, no shell parsing, report path or stdout JSON import, bounded stdout/stderr summaries, and `env_keys` evidence without secret values, external conformance execution readiness via `A2AExternalConformanceExecutionProfile`, external conformance execution record evidence via `A2AExternalConformanceExecutionRecord`, external conformance gate report evidence via `A2AExternalConformanceGateReport`, signed-card/trust primitives (`A2ACardSignature`, `HmacA2ACardSigner`, `HmacA2ACardVerifier`, `StaticA2ACardTrustStore`, `JwksA2ACardTrustStore`, `RotatingA2ACardTrustStore`, `RotatingHmacA2ACardSigner`), outbound A2A egress URL policy primitives (`PublicHttpsA2AEgressUrlPolicy`, `HostAllowListA2AEgressUrlPolicy`), outbound peer auth (`A2AAuthProvider`, `StaticBearerA2AAuthProvider`, `A2ABearerCredential`, `RotatingBearerA2ACredentialStore`, `RotatingBearerA2AAuthProvider`), inbound peer auth (`A2AInboundAuthPolicy`, `RejectAllA2AInboundAuthPolicy`, `StaticBearerA2AInboundAuthPolicy`, `RotatingBearerA2AInboundAuthPolicy`, `HmacA2AJwtVerifier`, `OidcDiscoveryMetadataProvider`, `JwksA2AJwtVerifier`, `OidcClaimsA2AInboundAuthPolicy`, `PeerAllowListA2AInboundAuthPolicy`, `OperationAllowListA2AInboundAuthPolicy`, `ResourceAllowListA2AInboundAuthPolicy`, `A2ATenantRbacRule`, `ClaimsTenantRbacA2AInboundAuthPolicy`), per-peer operation rate limiting (`A2AOperationRateLimitPolicy`, `PeerKeyA2AOperationRateLimitPolicy`, `A2APeerIdResolver`, `A2ARateLimitError`), `message/send`, `message/stream`, `A2AOperationClient.stream_message`, `A2AOperationClient.stream_message_events`, `A2AMessageStreamEvent`, `parse_a2a_sse_events`, message stream operation boundary, task lookup, task cancel, `tasks/resubscribe`, task subscribe operation boundary, task subscribe/SSE status update boundaries, `A2AStreamLifecycleDeploymentProfile` for stream lifecycle readiness metadata, push notification config primitives (`A2APushNotificationConfig`, `A2APushNotificationConfigStore`, `InMemoryA2APushNotificationConfigStore`, `PostgresA2APushNotificationConfigStore`), webhook URL validation primitives (`PublicHttpsA2APushNotificationUrlPolicy`, `HostAllowListA2APushNotificationUrlPolicy`), one-shot webhook delivery via `A2APushNotificationDispatcher`, local enqueue/claim/retry/dead-letter primitives via `A2APushNotificationDeliveryWorker`, `A2APushNotificationDaemon`, `A2APushNotificationDeploymentProfile`, `A2APushNotificationHealthPolicy`, `A2APushNotificationHealthReport`, `A2APushNotificationRetryPolicy`, and `InMemoryA2APushNotificationDeliveryStore`, plus durable webhook queue state via `PostgresA2APushNotificationDeliveryStore`
**Missing**: deployment process supervision and restart/alerting policy for webhook workers, CA trust rollout, tenant directory and role assignment lifecycle integration, DNS pinning/egress proxy SSRF controls beyond SDK URL policy, credential issuance/secret distribution/KMS governance, distributed/global quota storage, gateway enforcement, billing tiers, official external suite selection/installation, CI matrix execution, certification attestation, production skills/interfaces/auth mapping
**Workaround**: use the internal `AgentCard` for agent-os routing, expose an A2A Agent Card as a compatibility surface, sign cards before publication when the deployment has a trust boundary, configure `A2ACardResolver(card_verifier=...)` for trusted discovery, use `PublicHttpsA2AEgressUrlPolicy` or `HostAllowListA2AEgressUrlPolicy` as `egress_url_policy` on `A2ACardResolver`, `OidcDiscoveryMetadataProvider`, `JwksA2ACardTrustStore`, `JwksA2AJwtVerifier`, `A2AOperationClient`, or `A2AAdapter` when outbound discovery/JWKS/OIDC/peer calls must be restricted to public HTTPS hosts or explicit host/domain allow-lists, rely on the A2AOperationClient default public HTTPS egress policy when no explicit client egress policy is provided, use `JwksA2ACardTrustStore` only with explicitly configured HTTPS JWKS URLs and key-id allow-lists when card signing keys should be discovered rather than locally embedded, use `A2AOperationServer`/`A2AOperationClient` with `A2AProtocolVersionPolicy`, `A2AExtensionNegotiationPolicy`, and an `A2AAuthProvider` for `message/send`, `message/stream`, task get/cancel, `tasks/resubscribe`, and task subscribe/status updates; `A2AOperationServer` uses `RejectAllA2AInboundAuthPolicy` by default, default inbound A2A operation auth is fail-closed, A2AOperationServer default rejects unauthenticated peers before runner/store work, and `AllowAllA2AInboundAuthPolicy` is explicit local/dev opt-in so local/dev peers must opt in explicitly; use `A2AOperationClient.stream_message(...)` to initiate outbound `message/stream` through the same protocol/auth/extension/egress boundary as `send_message(...)`, and use `A2AOperationClient.stream_message_events(...)` with `A2AMessageStreamEvent`/`parse_a2a_sse_events(...)` to consume peer `text/event-stream` responses through the SDK's typed parser boundary while keeping reconnects, durable stream cursors, fan-out, and backpressure transport-owned; use `A2AStreamLifecycleDeploymentProfile` to expose readiness metadata for configured stream lifecycle components while keeping automatic reconnect loops, durable cursor storage, fan-out, backpressure, gateway quota, billing, credential issuance, process supervision, DNS pinning, enterprise egress proxy, CA rollout, tenant directory lifecycle, and external conformance execution deployment-owned; the ASGI `/a2a/message:stream` route uses the message stream operation boundary before SSE starts, and the ASGI subscribe route uses the task subscribe operation boundary before SSE starts, configure `RotatingBearerA2ACredentialStore` with `A2ABearerCredential` entries plus `RotatingBearerA2AAuthProvider` and `RotatingBearerA2AInboundAuthPolicy` when bearer credentials need an SDK-owned current-token and overlap-window boundary, configure `OidcDiscoveryMetadataProvider` when OIDC issuer metadata should be fetched, exact-match validated, cached, and exposed as a JWKS URI, configure `JwksA2AJwtVerifier` with the optional `security` extra when inbound peer auth should verify RS256/JWKS JWT signatures, configure `OidcClaimsA2AInboundAuthPolicy` with `HmacA2AJwtVerifier` or `JwksA2AJwtVerifier` when inbound peer auth should validate JWT/OIDC claims, configure `A2AInboundAuthPolicy`, `PeerAllowListA2AInboundAuthPolicy`, `OperationAllowListA2AInboundAuthPolicy`, `ResourceAllowListA2AInboundAuthPolicy`, `RotatingBearerA2AInboundAuthPolicy`, or `ClaimsTenantRbacA2AInboundAuthPolicy` with `A2ATenantRbacRule` for inbound peer authorization, use `ClaimsTenantRbacA2AInboundAuthPolicy` when verified JWT/OIDC claims should authorize tenant-scoped roles, scopes, operations, and task/resources while tenant directory, role assignment lifecycle, IdP administration, credential issuance, secret distribution, and KMS/secret-manager governance stay deployment-owned, pass `PeerKeyA2AOperationRateLimitPolicy` to `A2AOperationServer(rate_limit_policy=...)` when public A2A calls need a local per-peer rate limit after auth and before runner/store work, use `A2APeerIdResolver` to derive peer ids from already authenticated headers or claims, keep distributed/global quota storage, gateway enforcement, and billing tiers deployment-owned, use `A2APushNotificationConfigStore` for webhook configuration, configure `HostAllowListA2APushNotificationUrlPolicy` on push config stores and dispatchers when webhook hosts must be restricted, use `A2APushNotificationHealthPolicy` or `A2APushNotificationDaemon.health()` to classify worker polling state for process supervisors, use `A2APushNotificationDeploymentProfile.health_check()` and `.readiness_check()` to wire that state into deployment probes or `AsgiAgentApp(readiness_checks=...)`, use `PostgresA2APushNotificationConfigStore` and `PostgresA2APushNotificationDeliveryStore` when state must cross nodes, and run `A2APushNotificationDaemon` from a supervised service process.

### 20. Minimal A2A Task Bridge
**Available**: outbound `A2AAdapter`, inbound `A2AServerAdapter`, `/a2a/tasks`, `/a2a/health`, W3C trace propagation, A2A Agent Card publication/discovery primitives, protocol version negotiation primitives, extension negotiation primitives, A2A 1.0 text/file/data part payload shapes, `A2AArtifact`, `statusUpdate`/`artifactUpdate` wrapper serializers, SDK self-conformance reports via `A2AConformanceHarness` including `message/stream request`, typed `message stream event`, `tasks/resubscribe request`, and task resubscribe statusUpdate event checks, external conformance result import via `A2AExternalConformanceReportImporter`, external conformance invocation planning via `A2AExternalConformanceInvocationPlan` and `A2AExternalConformanceInvocationGateReport`, external conformance execution readiness via `A2AExternalConformanceExecutionProfile`, signed-card/trust primitives, outbound peer auth provider primitives, outbound A2A egress URL policy primitives, plus `A2AOperationServer`/`A2AOperationClient` for `/a2a/message:send`, `A2AOperationClient.stream_message(...)`, `A2AOperationClient.stream_message_events(...)`, `A2AMessageStreamEvent`, `parse_a2a_sse_events(...)`, and `/a2a/message:stream`, `/a2a/tasks/{id}`, `/a2a/tasks/{id}:cancel`, `POST /a2a/tasks/{id}:subscribe`, and `/a2a/tasks/{id}/pushNotificationConfigs` config routes
**Missing**: full A2A protocol binding, deployment process supervision/monitoring for webhook workers, DNS pinning/egress proxy SSRF controls beyond SDK URL policy, CA trust rollout, complete protocol semantic parity, and external suite execution automation/certification attestation
**Workaround**: use it only as an internal JSON task bridge between agent-os services.

### 21. RAG Agent
**Available**: `RecallRuntime` + `MemoryRuntime` + `QdrantRecallIndex` + query recall
**Missing**: document ingestion pipeline, chunking, embedding, indexing policy
**Workaround**: implement `RecallIndex.index_segment()` with your own chunker, or feed documents as `CompressedSegmentPackage`.

### 22. Scheduled / Cron Agent
**Available**: `AsgiAgentApp` + HTTP channel
**Missing**: built-in scheduler
**Workaround**: external cron or task queue calls the turn endpoint on schedule.

### 23. Human-in-the-Loop Agent
**Available**: `HookManager.before_tool_call` deny/modify plus callback events
**Missing**: standardized approval UI protocol
**Workaround**: hook denies sensitive tools, returns an awaiting-approval message, and an external UI calls back to approve.

## Future Extensions

| Form | Gap |
|------|-----|
| Runtime Profile Agent | `LocalRuntimeProfile`, `WebRuntimeProfile`, `DistributedWebRuntimeProfile`, `DistributedTeamRuntimeProfile`, and `DistributedAgentProfile` exist; remaining work is OS/container sandboxing, A2A deployment profiles, scheduler/background profiles, and deeper enterprise governance presets. |
| Team Discussion Agent | Team records/messages, in-memory/Postgres stores, wakeup notices, `TeamTools`, worker session lifecycle boundaries, `TeamWorkerPermissionPolicy`, `WorkspaceToolSandboxPolicy`, `TeamWorkerRunner`, `TeamWorkerDaemon`, `WorkerProcessLifecycleDeploymentProfile`, persistent retry/backoff primitives, persistent cancellation intent primitives, UI event stream protocol, JSON replay endpoint, SSE/follow endpoint, distributed UI stream store, and `DistributedTeamRuntimeProfile` preset are ready; OS/container sandboxing and deployment-owned worker supervision/scaling execution remain future work. |
| Planner / Intent-Router Agent | Planner tools/primitives, raw LLM decomposition proposal gate through `PlannerRuntime.gate_decomposition_proposal` / `plan_gate_decomposition_proposal`, structured decomposition validation/ingestion, dependency metadata, ready-step query, schedulable plan selection through `PlannerRuntime.schedulable_plans` / `plan_schedulable_plans` and `PlannerSchedulablePlan`, local and Postgres-backed plan claim/lease through `PlanClaimStore` / `PostgresPlanClaimStore` / `plan_claim_schedulable_plans`, claim-before-tick scheduler boundary through `PlannerRuntime.claimed_scheduler_tick` / `plan_claimed_scheduler_tick`, claimed scheduler daemon polling through `PlannerClaimedSchedulerDaemon`, planner scheduler governance profile through `PlannerSchedulerGovernanceDeploymentProfile`, stale claim sweep boundary through `PlannerRuntime.sweep_expired_claims`, bounded ready-step dispatch report, one-shot scheduler tick, `PlannerSchedulerDaemon` local polling over explicitly supplied plan ids, failure/retry metadata, retryable-step query, retry reset, `PostgresPlanStore`, `docs/migrations/2026-06-16-postgres-plan-claims.sql`, `PlannerStaleClaimSweepProfile`, `PlannerDecompositionPolicyDeploymentProfile`, `PlannerLlmDecompositionGovernanceProfile`, `PlannerOrchestrationDeploymentProfile`, and `WorkerProcessLifecycleDeploymentProfile` are available; automatic LLM decomposition prompt/model/approval/evaluation policy, tenant routing, global fairness, distributed scheduler locks, stale claim sweep scheduling policy, process supervision, worker lifecycle execution, and complex compensation remain app-owned/future work. |
| Workspace-Isolated Production Agent | Workspace primitives, policy, team worker workspace narrowing, `WorkspaceToolSandboxPolicy` path/capability checks, `WorkspaceExecutionIsolationProfile` readiness metadata, and `WorkspaceExecutionBackend` / `SandboxBackend` with `LocalWorkspaceExecutionBackend` reference execution are available; Docker/E2B/enterprise runner adapters and OS/container sandboxed execution remain deployment-owned future work. |
| Graph / DAG Workflow Agent | No state graph engine; only linear tool loop and planner step records. |
| Code Interpreter Agent | No sandboxed execution environment. |
| Voice / Multimodal Streaming Agent | Provider supports ImagePart/FilePart but no audio streaming. |
| Self-Evolving Agent | No meta-learning or self-prompt-modification. |
| Browser Automation Agent | No built-in browser driver integration. |

## Decision Tree

```text
User wants to build an agent:
- Single conversation, no tools?        -> Form 1
- Needs to call APIs / execute code?    -> Form 2
- Connects to MCP servers?              -> Form 3
- Real-time streaming UI?               -> Form 4
- Long conversations?                   -> Form 5
- Running in async framework?           -> Form 6
- Single-process HTTP service?          -> Form 7
- Needs snapshot persistence?           -> Form 8
- Multiple specialist agents?
  - Same process?                       -> Form 9
  - Cross-service?                      -> Form 16
- Intent router / plan-and-execute?     -> Form 18
- Needs monitoring / tracing?           -> Form 10
- Needs cancel / timeout?               -> Form 11
- Approval / filtering required?        -> Form 12 or 23
- Custom UI progress callbacks?         -> Form 13
- Event-driven wake-on-result?          -> Form 14
- Any node can serve any session?        -> Form 15
- Team discussion?                      -> Form 17
- Agent registry/discovery?             -> Form 19
- A2A interoperability?                 -> Form 20 now; full protocol later
- Search over past conversations/docs?  -> Form 21
- Runs on schedule?                     -> Form 22
```

## Combining Forms

Most production agents combine multiple forms. Be explicit about readiness:

| Combo | Forms |
|-------|-------|
| Local terminal agent | 1 or 2 + 5 + 10 |
| Single-node API agent | 2 + 4 + 7 + 10 + 11 |
| Production web distributed agent | 2 + 4 + 10 + 15 + deployment policy work |
| Research assistant | 2 + 5 + 10 + 18 + 21 |
| Local multi-agent service | 9 + 7 + 10 + 12 |
| Distributed multi-agent service | 16 + 15 + 17 + 18 + 19 + 20 + retry/permission/profile glue |
| Chat with tools + memory | 2 + 4 + 5 + 8 + 21 |
| Enterprise compliance agent | 2 + (7 or 15) + 10 + 12 + 23 |

## Production Readiness Notes

- Terminal/script agents are stable with sync `QueryLoop`. Do not force async unless the host application already has an event loop or async tool handlers.
- Web agents should prefer native `AsyncQueryLoop`; standard multi-node deployments should use `DistributedWebRuntimeProfile` with concrete distributed lease/snapshot adapters and `DistributedWebSessionOperationsProfile` for distributed session operations readiness, while still defining workspace, TTL, recovery, auth, migration, credentials, and live backend policy.
- Production web agents may use the Agent Service Reference Layer when they need a reference service composition instead of a platform. Use `AgentServiceReference`, `AgentServiceReferenceProfile`, and `AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS` for AsgiAgentApp composition, DistributedWebRuntimeProfile injection, auth/rate-limit hook injection, readiness check aggregation, and JSON-safe readiness evidence. The reference service is not a platform; gateway/TLS/CORS/WAF, tenant directory integration, Kubernetes/systemd/autoscaling, credentials, migrations, alerting, and live backend verification remain deployment-owned.
- Production web agents may use `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, and `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` when the spec needs a reference state plane tying `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`, `AgentServiceReference`, `DistributedWebRuntimeProfile`, and `ProductionReadinessEvidenceBundle` together through readiness source aggregation and component identity evidence. It does not create backend clients; credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned.
- Use `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`, and `ReadinessEvidenceStatus` as the final release gate evidence bundle when existing readiness/profile/backend evidence has already been produced. It consumes existing readiness/profile/backend evidence, emits `blocking_checks`, `missing_required_checks`, `block_production_readiness`, `sdk_owned`, `deployment_owned`, and a JSON-safe evidence bundle, and does not execute real infrastructure checks.
- Production distributed agents should also use `ProductionStatePlaneDeploymentProfile` to separate registry, queue, task truth, plan truth, worker lifecycle evidence, and session snapshot state. The recommended state-plane boundaries are `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, and `SessionSnapshotPersistence`; registry discovery should expose Nacos namespace_id evidence, Nacos namespace_id is passed to register, unregister, list, resolve, and discover, registry is not task truth, and queue is not final task or plan state.
- Workspace handles are SDK primitives. Use `WorkspaceToolSandboxPolicy` and `ToolPathSandboxRule` to reject declared path escapes and unauthorized capabilities before external tool handlers run; use `WorkspaceExecutionIsolationProfile` to report configured `workspace_policy`, `tool_path_sandbox`, `capability_allowlist`, `execution_backend`, `process_isolation`, `resource_limits`, `network_policy`, and `audit_logging` components. Use `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, and `WorkspaceExecutionPolicy` when a tool or subagent needs a swappable execution backend. `LocalWorkspaceExecutionBackend` is the argv-only local reference backend, emits JSON-safe execution evidence with `env_keys`, and does not inherit the host environment by default; it is not a production isolation boundary. SDK-owned protections stop at path escape pre-check, tool capability pre-check, local reference execution, and JSON-safe evidence; Docker/E2B/enterprise runner adapters, OS/container sandboxing, filesystem mount policy, network egress policy, CPU and memory limits, secret redaction, audit logging backend, and live sandbox backend verification remain deployment-owned.
- `SessionSnapshot`, Redis hot state, and Postgres durable memory are different projections. Do not treat `RedisHotSessionStore` as a drop-in `SessionPersistence` unless an adapter explicitly implements that protocol.
- Current A2A support is an internal task bridge with A2A Agent Card publication/discovery primitives, protocol version negotiation via `A2AProtocolVersionPolicy`, extension negotiation via `A2AExtensionNegotiationPolicy`, A2A 1.0 text/file/data part payload shapes with legacy text/file/data input compatibility, `A2AArtifact`, `statusUpdate`/`artifactUpdate` wrapper serializers, SDK self-conformance reports via `A2AConformanceHarness` including `message/stream request`, typed `message stream event`, `tasks/resubscribe request`, and task resubscribe statusUpdate event checks, external conformance result import via `A2AExternalConformanceReportImporter`, external conformance invocation planning via `A2AExternalConformanceInvocationPlan` and `A2AExternalConformanceInvocationGateReport`, external conformance CLI runner reference execution via `A2AExternalConformanceRunner` and `A2AExternalConformanceCliRunner`, external conformance execution readiness via `A2AExternalConformanceExecutionProfile`, external conformance execution record evidence via `A2AExternalConformanceExecutionRecord`, external conformance gate report evidence via `A2AExternalConformanceGateReport`, signed-card/trust verification primitives, local HMAC card key rotation primitives, JWKS `oct`/HS256 card key discovery through explicitly configured HTTPS URLs, OIDC discovery metadata through `OidcDiscoveryMetadataProvider`, RS256/JWKS JWT verification through `JwksA2AJwtVerifier`, outbound A2A egress URL policies through `PublicHttpsA2AEgressUrlPolicy` and `HostAllowListA2AEgressUrlPolicy`, A2AOperationClient default public HTTPS egress policy, outbound peer auth header injection, bearer credential rotation primitives (`A2ABearerCredential`, `RotatingBearerA2ACredentialStore`, `RotatingBearerA2AAuthProvider`, `RotatingBearerA2AInboundAuthPolicy`), inbound peer auth policy enforcement through `RejectAllA2AInboundAuthPolicy` by default, default inbound A2A operation auth is fail-closed, A2AOperationServer default rejects unauthenticated peers, `AllowAllA2AInboundAuthPolicy` is explicit local/dev opt-in, local/dev peers must opt in explicitly, JWT/OIDC claims validation through `OidcClaimsA2AInboundAuthPolicy`, inbound peer allow-list policy, operation allow-list policy, claims-backed tenant RBAC policy, local per-peer rate limit primitives (`A2AOperationRateLimitPolicy`, `PeerKeyA2AOperationRateLimitPolicy`, `A2APeerIdResolver`, `A2ARateLimitError`), plus `message/send`, `message/stream`, `A2AOperationClient.stream_message`, `A2AOperationClient.stream_message_events`, `A2AMessageStreamEvent`, `parse_a2a_sse_events`, message stream operation boundary, task get/cancel, `tasks/resubscribe`, `A2AOperationClient.task_resubscribe` for one-shot cursor resubscribe, task subscribe operation boundary, task subscribe/status updates, `A2AStreamLifecycleDeploymentProfile` readiness metadata for stream lifecycle components, push notification configuration routes, configurable webhook URL allow-list policy, one-shot webhook delivery, local enqueue/claim/retry/dead-letter worker primitives, an `A2APushNotificationDaemon` polling loop, `A2APushNotificationDeploymentProfile`, `A2APushNotificationHealthPolicy`/`A2APushNotificationHealthReport` health projection, and Postgres durable push config/delivery stores. Use `A2AExternalConformanceInvocationPlan` and `A2AExternalConformanceInvocationGateReport` before deployment-owned CI runs an external suite to capture suite id/version, command, target, required checks, credential/network/version/artifact/alerting policy references, and no-certification-claim preflight evidence. Use `A2AExternalConformanceCliRunner` when a local or CI process needs the SDK reference adapter: it executes argv-only with no shell parsing, imports a report path or stdout JSON, captures bounded stdout/stderr summaries, records `env_keys`, redacts secret values, and returns an `A2AExternalConformanceExecutionRecord`. Use `A2AExternalConformanceExecutionProfile` to report required checks (`agent-card`, `message-send`, `message-stream`, `tasks-resubscribe`, `push-notification-config`) and configured components (`external_suite_runner`, `target_endpoint`, `credential_policy`, `network_egress_policy`, `version_matrix`, `ci_artifact_retention`, `failure_alerting`) without making a certification claim. Use `A2AExternalConformanceExecutionRecord` and `A2AExternalConformanceGateReport` after the external suite has been invoked to capture command/target/exit/artifact evidence and evaluate a local release gate with no certification claim. Do not market it as full A2A compliance until automatic reconnect loops, durable cursor storage, fan-out, backpressure, gateway quota, billing, credential issuance, process supervision, DNS pinning, enterprise egress proxy, CA rollout, tenant directory lifecycle, official external suite selection/installation, CI matrix execution, and certification attestation governance land.
- AllowAllA2AInboundAuthPolicy is explicit local/dev opt-in.
- Team discussion primitives include `DistributedTeamRuntimeProfile` for the standard preset assembly, `PostgresTeamStore` for multi-node state, `TeamWorkerSessionProvider` for worker session registration, `TeamWorkerPermissionPolicy` for workspace/capability downgrade at session creation, `WorkspaceToolSandboxPolicy` for worker tool path/capability pre-checks, `TeamWorkerRunner` for batch continuation execution, `TeamWorkerDaemon` for service-hosted polling, `WorkerProcessSpec`/`WorkerProcessState`/`WorkerProcessSupervisor`/`LocalSubprocessWorkerSupervisor` for JSON-safe lifecycle evidence, WorkerProcessSpec rejects secret-like metadata keys, `PostgresTeamWorkerRetryStore` for persistent retry/backoff state, `PostgresTeamWorkerCancellationStore` for persistent cancellation intent before execution, `TeamUiStreamStore` plus `PostgresTeamUiStreamStore` for UI replay projection, `GET /v1/teams/{team_id}/ui-events` for JSON replay, and `GET /v1/teams/{team_id}/ui-events/stream` for SSE follow. Redis/Postgres credentials, migration execution, process supervision, worker scaling, live backend verification, and OS/container sandboxing remain deployment-owned.
- Planner tools/primitives define state, templates, raw LLM decomposition proposal gating through `PlannerRuntime.gate_decomposition_proposal`, `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`, and `plan_gate_decomposition_proposal`, structured decomposition validation/ingestion through `PlannerRuntime.validate_decomposition`, `PlanDecompositionValidationReport`, and `plan_create_from_decomposition`, dependency metadata, ready-step queries, schedulable plan selection through `PlannerRuntime.schedulable_plans`, `plan_schedulable_plans`, and `PlannerSchedulablePlan`, local and Postgres-backed plan claim/lease through `PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`, `PlannerRuntime.claim_schedulable_plans`, and `plan_claim_schedulable_plans`, claim-before-tick scheduler batches through `PlannerRuntime.claimed_scheduler_tick`, `plan_claimed_scheduler_tick`, `PlanClaimedSchedulerTickReport`, and `PlanClaimedSchedulerTickSkip`, dispatch supervision payloads through `PlannerWorkerDispatchSupervisionProfile`, stale claim sweep reports/releases through `PlanClaimSweepReport`, `PlanClaimSweepSkip`, `PlanClaimSweepStore`, `PlannerRuntime.sweep_expired_claims`, and `PlannerStaleClaimSweepProfile`, bounded ready-step dispatch through `plan_dispatch_ready_steps`, one-shot scheduler passes through `plan_scheduler_tick`, a `PlannerSchedulerDaemon` polling loop over explicitly supplied plan ids with `PlannerSchedulerDaemonState`, failure/retry metadata through `PlanRetryPolicy`, assignments, evidence handles, persistent plan storage through `PostgresPlanStore`, `docs/migrations/2026-06-16-postgres-plan-claims.sql`, `PlannerDecompositionPolicyDeploymentProfile` readiness metadata for `prompt_policy`, `output_schema`, `validation_gate`, `template_mapping_policy`, `approval_policy`, `model_routing_policy`, `evaluation_policy`, `trace_logging`, and `rollback_policy`, `PlannerLlmDecompositionGovernanceProfile` governance reference readiness payloads for `component_refs`, `evidence_refs`, and `budget_policy` while keeping prompt text and prompt review workflow deployment-owned, `PlannerOrchestrationDeploymentProfile` readiness metadata for `decomposition_policy`, `dag_scheduler`, `worker_dispatch_loop`, `compensation_policy`, `plan_store`, and `worker_supervision`, `WorkerProcessSpec`/`WorkerProcessState`/`WorkerProcessSupervisor`/`LocalSubprocessWorkerSupervisor` for local `planner_worker` JSON-safe lifecycle evidence, and `PlannerSchedulerGovernanceDeploymentProfile` readiness metadata for `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `scheduler_lock_policy`, `leader_election_policy`, `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and `live_backend_verification`. They do not yet provide the automatic LLM decomposition prompt/model/approval/evaluation policy execution, plan discovery sources, distributed scheduler locks, stale claim sweep scheduling policy, production process supervision, worker dispatch loop execution, production worker process lifecycle management, or complex compensation orchestration.
