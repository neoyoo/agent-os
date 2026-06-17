---
name: agent-os-sdk
description: >
  Use when building agents with the agent-os SDK; guides requirements gathering,
  architecture decisions, spec generation, and parallel implementation handoff.
  Triggers on: "build an agent", "create agent", "new agent project", "use agent-os".
---

# agent-os SDK - Agent Development Skill

## Overview

Guides a developer from idea to running agent using the agent-os SDK. The process is phased: understand what they are building, generate a spec, then hand off implementation.

**agent-os is a harness SDK**: it provides runtime, context, messages, providers, compression, hooks, channels, multi-agent, memory, persistence, workspace, and observability primitives. The developer configures and extends it; no subclassing or framework lock-in is required.

## Release Scope Re-baseline

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

## SDK Skill / Spec Generator Finalization

Phase 99: SDK Skill / Spec Generator Finalization makes this skill a spec
generator finalization gate and production agent design constraint generator.
For production-bound agents, the spec must include an SDK-owned constraint
template named `production_design_constraints`.

The `production_design_constraints` block must explicitly choose agent form,
runtime profile, state plane components, persistence backend, registry backend,
queue backend, worker supervisor, A2A exposure, planner/team mode, production
readiness checklist, and sandbox posture: trusted tools only |
deployment-owned isolation | future adapter. The spec must also state that the
SDK does not create deployment-owned infrastructure.

Treat missing `production_design_constraints` as a blocker before
implementation handoff. The SDK-owned constraint template records design
decisions and readiness expectations; deployment remains responsible for real
infrastructure, credentials, migrations, process supervision, backend probe
execution, tenant directory integration, and physical isolation.

## Release Hardening

Phase 100: Release Hardening makes the review branch a release-candidate SDK
branch. Before calling a production-bound spec ready for release, require the
release hardening gate from `docs/release-hardening.md`: release candidate
evidence for public API audit, stable API and experimental API classification,
migration index, README / quickstart / examples alignment, `CHANGELOG.md`,
full test suite evidence, diff/commit hygiene, and runtime boundary scans.

Use `docs/api-stability.md` for API stability classification and
`docs/migrations/README.md` for the migration index. This is SDK-owned release
evidence. AgentOS does not run CI/CD, signing, publishing, deployment approval;
those operations remain deployment-owned.

## Production Reference Example

Phase 101: Production Reference Example adds the production reference web
agent at `src/agentos/examples/production_reference_web_agent.py`, with tests
in `tests/examples/test_production_reference_web_agent.py`. Use it as the
copyable reference when a production-bound web agent spec needs
`AgentServiceReference`, `DistributedWebRuntimeProfile`, a
Nacos/Redis/Postgres state plane, a readiness endpoint, backend verification,
`ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, and a planner primitive in one composition.

This reference proves SDK composition, not platform provisioning. It does not
create backend clients. Nacos, Redis, Postgres, credentials, migrations, live
backend probe execution, Kubernetes/systemd, autoscaling, CI/CD, gateway/TLS,
tenant directory integration, alerting, runbooks, rollout, rollback, and
sandbox isolation are deployment-owned real infrastructure.
The demo runtime blocks production readiness by default; imported backend
verification evidence does not make a Memory/InMemory served app production
ready unless `allow_demo_runtime_readiness` is explicitly used as a demo
fixture.

## When to Use

- Developer says they want to build an agent.
- Developer asks how to use agent-os for a specific use case.
- Developer has requirements and wants architecture guidance.
- Developer wants to understand which agent-os modules apply to their problem.

## Process

```text
flow/01-requirements.md    -> phased questions to understand the agent
flow/02-spec-generation.md -> produce agent-spec.yaml from answers
flow/03-implementation.md  -> hand off to implementation workflow
```

## Routing

| User intent | Load |
|-------------|------|
| "I want to build an agent" / "new agent" | `flow/01-requirements.md` |
| "Generate the spec" / already answered questions | `flow/02-spec-generation.md` |
| "Start implementation" / spec exists | `flow/03-implementation.md` |
| "What kind of agent can I build?" / capability check | `modules/agent-forms.md` |
| Specific module question | `modules/<module>.md` |

## Skill Release Governance

The repository skill is `.claude/skills/agent-os`. Before publishing or
depending on an installed user-level skill, use `SkillReleaseManifest`,
`SkillReleaseFile`, `build_skill_release_manifest(...)`,
`SkillReleaseDriftReport`, and `compare_skill_release_manifests(...)` from
`agentos.skills` to compare the repository skill with the installed user-level
skill. Treat the release manifest and drift report as the SDK-owned version synchronization boundary.

The install/copy/publish/sign approval remains deployment-owned: do not
silently overwrite user skills, publish marketplace entries, sign artifacts, or
run plugin cache busting from the SDK boundary.

## Reference State Plane Stack

Phase 97: Reference State Plane Stack adds `ReferenceStatePlaneStack`,
`ReferenceStatePlaneStackProfile`, and
`REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` as the SDK-owned reference state
plane composition. Use it when a production spec needs to prove that
`NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`,
`PostgresPlanStore`, `WorkerProcessSupervisor`,
`LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`,
`PostgresSessionSnapshotPersistence`, `AgentServiceReference`,
`DistributedWebRuntimeProfile`, and `ProductionReadinessEvidenceBundle` can be
combined through readiness source aggregation and component identity evidence.

The reference state plane does not create backend clients. It records
JSON-safe audit evidence over already configured adapters and profiles.
credentials, migrations, CI matrix execution, alert routing and runbooks remain
deployment-owned.

## Live Backend Probe Pack

Phase 98: Live Backend Probe Pack adds `ReferenceLiveBackendProbePack`,
`ReferenceLiveBackendProbeSpec`, and
`REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME`. Use it when a production spec needs
a repeatable reference Nacos probe, Redis probe, Postgres task/plan/session
probe, and worker supervisor probe plan that feeds backend verification
evidence into `ProductionReadinessEvidenceBundle` through readiness bundle
aggregation.

The default reference command is `agentos.examples.live_backend_probe`, which
emits stdout JSON compatible with `BackendVerificationReportImporter`.
`ReferenceLiveBackendProbePack` produces `BackendVerificationInvocationPlan`
values and aggregates `DeploymentLiveBackendVerificationRunResult` evidence.
It does not create backend clients. Its default status is unknown and it is a
non-certifying example; deployments must run their own live backend checks and
explicitly provide passed records with external evidence references.
credentials, migrations, CI matrix execution, alert routing and runbooks remain
deployment-owned.

## SDK Source Location

The agent-os SDK lives at the project root. Key paths:

```text
src/agentos/
├── runtime/          -> Agent, QueryLoop, AsyncQueryLoop, ProviderRequestBuilder, RuntimeProfile
├── providers/        -> Provider protocol, Anthropic/OpenAI adapters, typed messages
├── context/          -> ContextRuntime, ContextRenderer, WorkingState, Chapters
├── messages/         -> MessageRuntime, Message, MessageRef
├── capabilities/     -> ToolCallRouter, ToolRegistry, RegisteredTool, WorkspaceToolSandboxPolicy, MCP adapters
├── compression/      -> CompressionRuntime, RuleBasedCompressor, LlmCompressor
├── hooks/            -> HookManager, HookRegistry, lifecycle hook points
├── channels/         -> AsgiAgentApp, HttpAgentChannel, durable sessions, internal A2A bridge
├── multi/            -> AgentCoordinator, TeamRuntime, PlannerRuntime, PlannerTools, task stores, queues
├── memory/           -> MemoryRuntime, HotSessionStore, DurableSessionStore, Redis/Postgres
├── persistence/      -> SessionSnapshot, SessionPersistence, SQLite/FileSystem/Memory
├── observability/    -> TraceContext, W3C propagation, EventRecord
├── workspace.py      -> WorkspaceHandle, WorkspacePolicy, WorkspaceProvider
├── context_protocol.py -> built-in context tools
└── builder.py        -> AgentBuilder (recommended entry point)
```

## Module Dependency Graph

```text
AgentBuilder
  -> Provider (Anthropic / OpenAI / custom)
  -> ToolCallRouter
     -> Context Protocol Tools (built-in, always wired)
     -> External Tools (RegisteredTool + handler)
     -> MCP Tools (optional)
  -> ContextRuntime -> ContextRenderer -> system prompt
  -> MessageRuntime -> active message window
  -> CompressionRuntime (optional) -> long sessions
  -> HookManager (optional) -> lifecycle interception
  -> EventBus (optional) -> typed event observation

Channels (ASGI / internal A2A task bridge) feed requests to Agent.
Persistence (SessionSnapshot + Redis/Postgres primitives) externalizes state.
Multi-agent (Coordinator + TeamRuntime + PlannerRuntime + PlannerTools) orchestrates subagents, team messages, structured decomposition validation/ingestion, dependency-aware plans, failure/retry metadata, assignments, and evidence handles.
```

## Multi-Agent Runtime Boundaries

Use `AgentCoordinator` for orchestration. Inject protocol boundaries when a deployment needs distributed task state or message delivery:

| Boundary | In-memory adapter | Production adapter |
|----------|-------------------|--------------------|
| `TaskStore` | `TaskTable` | `PostgresTaskStore` |
| `AgentMessageQueue` | `AgentInbox` | `RedisAgentMessageQueue` |
| `TeamStore` | `InMemoryTeamStore` | `PostgresTeamStore` |
| `TeamWorkerSessionProvider` | `InMemoryTeamWorkerSessionProvider` | app/profile provider |
| `TeamWorkerRunner` | batch runner | `TeamWorkerDaemon` host loop |
| `TeamWorkerDaemon` | service polling loop | app/profile UI stream |
| `WorkerProcessLifecycleDeploymentProfile` | JSON-safe worker lifecycle readiness | deployment supervisor/job runner |
| `DeploymentLiveBackendVerificationProfile` | `BackendVerificationRecord` evidence gate with `DeploymentLiveBackendVerificationGateReport`, `LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS`, `deployment_live_backend_verification`, and `block_production_readiness` for `agent_registry`, `message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, and `session_snapshot_persistence` | backend check execution, credentials and secret distribution, CI matrix execution, alert routing and runbooks |
| `BackendVerificationCliRunner` | `BackendVerificationInvocationPlan`, `BackendVerificationRunner`, `BackendVerificationReportImporter`, `BackendVerificationReportImportError`, and `DeploymentLiveBackendVerificationRunResult` reference runner for argv-only backend verification scripts, report path or stdout JSON import, bounded stdout/stderr summaries, `env_keys`, redacted secret values, no shell parsing, no backend client claim, and not a live backend client | Nacos/Redis/Postgres/supervisor/session clients, credentials, migrations, CI matrix execution, alert routing, runbooks, release approval, and certification |
| `ReferenceLiveBackendProbePack` | `ReferenceLiveBackendProbeSpec`, `REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME`, `agentos.examples.live_backend_probe`, Nacos probe, Redis probe, Postgres task/plan/session probe, worker supervisor probe, `BackendVerificationInvocationPlan`, `DeploymentLiveBackendVerificationRunResult`, readiness bundle aggregation, and `ProductionReadinessEvidenceBundle`; does not create backend clients | credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned |
| `ProductionReadinessEvidenceBundle` | `ReadinessEvidenceCheck` and `ReadinessEvidenceStatus` release gate evidence bundle that consumes existing readiness/profile/backend evidence, emits `accepted`, `blocking_checks`, `missing_required_checks`, `block_production_readiness`, `sdk_owned`, `deployment_owned`, and a JSON-safe evidence bundle, and does not execute real infrastructure checks | backend check execution, credentials, migrations, CI matrix execution, rollout, rollback, alerting, runbooks, and release approval |
| `ReferenceStatePlaneStack` | `ReferenceStatePlaneStackProfile`, `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`, reference state plane, readiness source aggregation, component identity evidence, `ProductionReadinessEvidenceBundle`, and JSON-safe audit evidence over `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`, `AgentServiceReference`, and `DistributedWebRuntimeProfile`; does not create backend clients | credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned |
| `TeamWorkerRetryStore` | `InMemoryTeamWorkerRetryStore` | `PostgresTeamWorkerRetryStore` |
| `TeamWorkerCancellationStore` | `InMemoryTeamWorkerCancellationStore` | `PostgresTeamWorkerCancellationStore` |
| `TeamWorkerPermissionPolicy` | workspace/capability downgrade checks | app/profile worker policy |
| `WorkspaceToolSandboxPolicy` | path/capability pre-execution checks | app/profile OS/container sandbox |
| `TeamUiStreamStore` | `InMemoryTeamUiStreamStore` | `PostgresTeamUiStreamStore` |
| `PlanStore` | `InMemoryPlanStore` | `PostgresPlanStore` |
| `PlannerRuntime.gate_decomposition_proposal` / `plan_gate_decomposition_proposal` | raw LLM/main-agent proposal gate with JSON-safe `PlanDecompositionGateReport` | prompt/model/approval/evaluation policy |
| `PlannerRuntime.gate_llm_governance_evidence` | per-proposal governance evidence gate with `PlannerLlmGovernanceEvidenceRecord` and `PlannerLlmGovernanceEvidenceGateReport` | deployment-owned prompt/model/approval/evaluation/validation execution |
| `PlanRetryPolicy` | step failure/backoff metadata | app/profile scheduler |
| `PlannerRuntime.schedulable_plans` / `PlannerSchedulablePlan` | schedulable plan selection by owner/status/ready-or-retryable work | plan discovery sources, fairness, and process supervision |
| `PlanClaimStore` / `InMemoryPlanClaimStore` / `PostgresPlanClaimStore` | local and Postgres-backed plan claim/lease boundary and JSON-safe `PlanClaimRecord` | distributed locks, leader election, stale lease recovery policy, fairness, and process supervision |
| `PlannerRuntime.claimed_scheduler_tick` / `plan_claimed_scheduler_tick` | claim-before-tick scheduler boundary for worker batches | plan discovery sources, leader election, fairness, stale-lease sweepers, and process supervision |
| `PlannerClaimedSchedulerDaemon` | local claimed scheduler daemon polling over `PlannerRuntime.claimed_scheduler_tick(...)` | tenant routing, global fairness, distributed locks, leader election, process supervision, and compensation |
| `PlannerSchedulerGovernanceDeploymentProfile` | planner scheduler governance profile readiness for `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `scheduler_lock_policy`, `leader_election_policy`, `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and `live_backend_verification` | actual tenant routing, fairness queues, distributed locks, leader election, and live backend verification |
| `PlannerWorkerDispatchSupervisionProfile` | JSON-safe dispatch supervision payloads from `PlanClaimedSchedulerTickReport` history | real worker loop execution, process supervisor, locks, stale-lease sweepers, and compensation |
| `PlannerSchedulerDaemon` | explicitly supplied plan id polling loop | app/profile plan discovery, distributed locks, and process supervision |

`TaskStore` is the truth source for task/result state. `AgentMessageQueue` is delivery/notification only. `TeamStore` and `PlanStore` are state boundaries; they should not be replaced by shared chat transcripts. `DeploymentLiveBackendVerificationProfile` consumes `BackendVerificationRecord` values and returns `DeploymentLiveBackendVerificationGateReport` for `LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS`; the `deployment_live_backend_verification` probe sets `block_production_readiness` on missing or failed backend evidence for `agent_registry`, `message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, or `session_snapshot_persistence`, while backend check execution, credentials and secret distribution, CI matrix execution, alert routing and runbooks stay deployment-owned. `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`, and `ReadinessEvidenceStatus` provide a release gate evidence bundle that consumes existing readiness/profile/backend evidence, emits `blocking_checks`, `missing_required_checks`, `block_production_readiness`, `sdk_owned`, `deployment_owned`, and a JSON-safe evidence bundle, and does not execute real infrastructure checks. `TeamWorkerSessionProvider` registers independent worker sessions; `TeamWorkerPermissionPolicy` rejects worker workspaces that broaden scope or escape the team root and can enforce a capability allow-list; `WorkspaceToolSandboxPolicy` rejects declared path escapes and unauthorized tool capabilities before external handlers run; `TeamWorkerRunner` turns queued team messages into worker continuation turns; `TeamWorkerDaemon` hosts that runner as a start/stop/join polling loop; `PlannerRuntime.gate_decomposition_proposal(...)`, `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`, and `plan_gate_decomposition_proposal` parse, normalize, policy-check, approval-gate, and audit raw LLM/main-agent JSON proposals without mutating `PlanStore`; `PlannerLlmGovernanceEvidenceRecord`, `PlannerRuntime.gate_llm_governance_evidence`, and `PlannerLlmGovernanceEvidenceGateReport` provide a planner LLM governance execution evidence boundary and per-proposal governance evidence gate that blocks production plan creation when prompt/model/approval/evaluation/validation evidence is missing or failed while deployment-owned prompt/model/approval/evaluation/validation execution owns the actual prompt run, model router, approval workflow, evaluation suite, schema validator, artifact retention, and certification decision; `PlannerRuntime.schedulable_plans(...)` and `plan_schedulable_plans` provide schedulable plan selection as JSON-safe `PlannerSchedulablePlan` summaries; `PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`, `PlannerRuntime.claim_schedulable_plans(...)`, and `plan_claim_schedulable_plans` provide local and Postgres-backed plan claim/lease boundaries with JSON-safe `PlanClaimRecord` values while distributed scheduler locks, leader election, stale lease recovery policy, and fairness remain deployment-owned; `PlannerRuntime.claimed_scheduler_tick(...)` and `plan_claimed_scheduler_tick` provide a claim-before-tick scheduler boundary with `PlanClaimedSchedulerTickReport` and `PlanClaimedSchedulerTickSkip` so workers tick only plans they claimed; `PlannerClaimedSchedulerDaemon` hosts that claim-before-tick boundary as local claimed scheduler daemon polling with `PlannerClaimedSchedulerDaemonState` and `PlannerClaimedSchedulerDaemonError`; `PlannerSchedulerGovernanceDeploymentProfile` exposes planner scheduler governance profile readiness for `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `scheduler_lock_policy`, `leader_election_policy`, `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and `live_backend_verification` while the real routing, fairness, locking, election, stale recovery, dispatch execution, and backend probes remain deployment-owned; `PlannerWorkerDispatchSupervisionProfile` turns recent `PlanClaimedSchedulerTickReport` history into a dispatch supervision payload with `claimed_scheduler_tick_loop`, `metrics_alerting`, claim, busy, failed, release, and consecutive-failure readiness metadata; `PlannerSchedulerDaemon` hosts `PlannerRuntime.scheduler_tick(...)` as a start/stop/join polling loop for explicitly supplied plan ids; `WorkerProcessLifecycleDeploymentProfile` reports configured worker lifecycle components such as `process_supervisor`, `restart_policy`, `graceful_shutdown`, `health_probe`, `readiness_probe`, `scaling_policy`, `credential_policy`, `migration_policy`, `alerting`, and `live_backend_verification` while keeping the real process supervisor or job runner deployment-owned; `TeamWorkerRetryPolicy` and `TeamWorkerRetryStore` prevent failed worker deliveries from hot-looping; `TeamWorkerCancellationStore` lets runner/daemon skip queued or retry-delayed worker continuations before execution; `TeamUiStreamStore` projects team and worker events for UI replay without exposing runner internals, with `PostgresTeamUiStreamStore` plus ASGI JSON replay and SSE follow endpoints for multi-node UI reads.

## Production Readiness Boundary

When mapping a user request to agent-os, distinguish:

- **Direct**: terminal/script, sync or async loop, tool-calling, streaming, compression/recall, local HTTP, local multi-agent, observability.
- **Primitives ready**: web distributed session state, distributed task coordination, distributed team state, team worker session lifecycle, worker workspace/capability downgrade policy, workspace-aware tool path/capability sandbox policy, worker process lifecycle readiness, batch runner, daemon polling loop, persistent retry/backoff boundary, persistent cancellation intent boundary, team UI event stream protocol, team UI JSON replay endpoint, team UI SSE/follow endpoint, distributed team UI stream store, registry/discovery, internal A2A task bridge, team records/messages/wakeup/tools, planner tools/subagent templates, planner raw decomposition proposal gate, planner decomposition validation/profile boundary, planner step failure/retry metadata, planner schedulable plan selection, planner local and Postgres-backed plan claim/lease boundary, planner claim-before-tick scheduler boundary, planner claimed scheduler daemon polling, planner scheduler governance profile, planner worker dispatch supervision profile, planner scheduler daemon over explicitly supplied plan ids, persistent planner store, RAG ingestion, scheduled agents, HITL UI.
- **Future extension**: full A2A protocol compliance, automatic LLM decomposition prompt/model/approval/evaluation policy, planner tenant routing/global fairness policy, distributed scheduler locks, stale lease recovery policy, process supervision, planner worker dispatch execution/compensation policies, OS/container-level sandboxing for untrusted code execution.

Do not present Redis/Postgres primitives as an automatic multi-node web runtime. A production deployment still needs a session provider/profile that locks, hydrates, runs, saves, and releases a session across arbitrary nodes.
