---
name: agent-os-architecture
description: How agent-os SDK modules connect — data flow, boundaries, extension points. Read when you need to understand WHY the SDK is structured this way.
---

# Architecture Reference

## Production Shape Boundary

agent-os is currently a context-first runtime SDK. The next production architecture target is a profile-driven application SDK:

```text
Local terminal profile
Web distributed profile
Distributed multi-agent profile
```

Those deployment shapes should be expressed above the core loop. `QueryLoop` should continue to consume collaborators (`ContextRuntime`, `MessageRuntime`, `ProviderRequestBuilder`, `Provider`, `ToolCallRouter`, persistence/observability hooks) and must not branch on local vs web vs distributed mode.

Current status:

- Terminal/script agents are direct: `AgentBuilder().build()` + sync `QueryLoop`.
- Async/web host integration is direct at the loop level: `AgentBuilder().build_async()` + `AsyncQueryLoop`.
- ASGI HTTP/SSE is direct for single-process service use.
- Multi-node web sessions are primitives-ready: `DistributedWebRuntimeProfile` assembles `DurableAgentSessionProvider` with lease and snapshot adapters, and `DistributedWebSessionOperationsProfile` exposes readiness metadata for `durable_session_provider`, `lease_store`, `snapshot_persistence`, `snapshot_migration`, `lease_ttl_policy`, `stale_lease_recovery`, `credential_policy`, `auth_tenant_policy`, `workspace_policy`, and `live_backend_verification`; production Redis/Postgres credentials, migration execution, lease TTL tuning, stale lease recovery policy, auth and tenant integration, workspace policy configuration, and live backend verification remain deployment-owned.
- Production state-plane readiness is primitives-ready: `ProductionStatePlaneDeploymentProfile` exposes the `production_state_plane` contract for `agent_registry`, `message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, `session_snapshot_persistence`, `state_plane_boundary_policy`, and `live_backend_verification`. Recommended adapter boundaries are `NacosAgentRegistryAdapter`, `NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, `NacosRegistryEvidence`, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, and `SessionSnapshotPersistence`. The Nacos registry boundary provides AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering, capability-based discovery, Nacos namespace_id evidence, JSON-safe evidence, and discovery-only Nacos metadata. Nacos namespace_id is passed to register, unregister, list, resolve, and discover. It is not task truth, not plan truth, not session snapshot storage, not message queue, and not worker runtime state; credentials and live backend verification remain deployment-owned.
- Live backend verification evidence is primitives-ready: `BackendVerificationRecord`, `DeploymentLiveBackendVerificationGateReport`, `DeploymentLiveBackendVerificationProfile`, and `LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS` provide the `deployment_live_backend_verification` readiness gate for `agent_registry`, `message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, and `session_snapshot_persistence`. The gate sets `block_production_readiness` on missing or failed backend evidence; backend check execution, credentials and secret distribution, CI matrix execution, alert routing and runbooks remain deployment-owned.
- Live backend verification reference runner is primitives-ready: `BackendVerificationInvocationPlan`, `BackendVerificationRunner`, `BackendVerificationCliRunner`, `BackendVerificationReportImporter`, `BackendVerificationReportImportError`, and `DeploymentLiveBackendVerificationRunResult` provide a reference runner for deployment-owned checks. It is argv-only, uses no shell parsing, imports a report path or stdout JSON, records bounded stdout/stderr summaries and `env_keys`, redacts configured secret values, emits a no backend client claim, and is not a live backend client.
- Production readiness evidence bundling is primitives-ready: `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`, and `ReadinessEvidenceStatus` provide a release gate evidence bundle that consumes existing readiness/profile/backend evidence, reports `accepted`, `blocking_checks`, `missing_required_checks`, and `block_production_readiness`, includes `sdk_owned` and `deployment_owned` boundary metadata, emits a JSON-safe evidence bundle, and does not execute real infrastructure checks.
- Reference state plane composition is primitives-ready: Phase 97: Reference State Plane Stack adds `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, and `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` as the SDK-owned reference state plane. It combines `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`, `AgentServiceReference`, `DistributedWebRuntimeProfile`, and `ProductionReadinessEvidenceBundle` through readiness source aggregation and component identity evidence. It does not create backend clients; credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned.
- Worker process lifecycle evidence has a reference adapter:
  `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and
  `LocalSubprocessWorkerSupervisor`. Use it for local `team_worker`,
  `planner_worker`, or `a2a_push_worker` subprocess evidence with `exit_code`,
  `started_at`, `last_heartbeat_at`, `stop_requested_at`, `stopped_at`, and
  `env_keys`. `heartbeat` records an evidence timestamp only. It is argv-only,
  uses no shell parsing, does not inherit the host environment by default,
  WorkerProcessSpec rejects secret-like metadata keys, and is not a Kubernetes,
  systemd, autoscaling, health/readiness, restart-policy, or
  secret-distribution layer.
- Workspace primitives are direct: `WorkspaceHandle`, `WorkspaceProvider`, `WorkspacePolicy`, and `LocalWorkspaceProvider`.
- Production web workspace enforcement is primitives-ready: profiles can carry workspace metadata, `WorkspaceToolSandboxPolicy` can reject declared path escapes and unauthorized tool capabilities before handlers run, `WorkspaceExecutionIsolationProfile` exposes a readiness contract for configured `workspace_policy`, `tool_path_sandbox`, `capability_allowlist`, `execution_backend`, `process_isolation`, `resource_limits`, `network_policy`, and `audit_logging` components, and `WorkspaceExecutionBackend` / `SandboxBackend` define the pluggable execution backend contract. `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, and `LocalWorkspaceExecutionBackend` provide an argv-only local reference adapter with JSON-safe execution evidence and `env_keys`; it does not inherit the host environment by default. Docker/E2B/enterprise runner adapters remain deployment-owned, and the local adapter is not a production isolation boundary. OS/container sandboxing remains deployment-owned.
- A2A is currently an internal task bridge plus Agent Card publication/discovery primitives, not full A2A compliance.

Production readiness is tracked in `agentos.readiness`. That module is a static SDK capability matrix, not a live health checker. Use it to inspect required app glue across session state, concurrency, auth, rate limiting, timeout, retry, observability, workspace, protocol, persistence, and schema migration before calling a form production-ready.

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

## Core Loop Data Flow

```
User message
    │
    ▼
QueryLoop.run_turn_stream(message)
    │
    ├── 1. MessageRuntime.append(user_message)
    │
    ├── 2. HookManager.dispatch("before_provider_call")
    │
    ├── 3. ProviderRequestBuilder.build_request()
    │       ├── ContextRenderer.render(context_state)  → system prompt
    │       ├── MessageRuntime.active_window()         → messages
    │       └── tools (from ToolCallRouter.tool_specs()) → tool schemas
    │
    ├── 4. Provider.complete(request) → ProviderResponse
    │
    ├── 5. HookManager.dispatch("after_provider_call")
    │
    ├── 6. If response has tool_calls:
    │       ├── HookManager.dispatch("before_tool_call")
    │       ├── ToolCallRouter.execute_tool_call(tc)
    │       │       ├── Context protocol tool? → ContextRuntime mutation
    │       │       ├── MCP tool? → MCPToolAdapter.execute()
    │       │       └── External tool? → ToolExecutor → RegisteredTool.handler()
    │       ├── HookManager.dispatch("after_tool_call")
    │       ├── MessageRuntime.append(tool_result)
    │       └── GOTO step 2 (tool loop, max_tool_iterations=8)
    │
    ├── 7. MessageRuntime.append(assistant_message)
    │
    ├── 8. CompressionRuntime.maybe_compress() (if budget exceeded)
    │
    └── 9. Yield TurnStreamCompleted(content)
```

## Module Boundaries (Protocol-based)

Each module exposes a Protocol. QueryLoop only knows the Protocol, not the implementation.

| Module | Protocol | QueryLoop sees as |
|--------|----------|-------------------|
| Context | `ContextRuntimeBoundary` | `.snapshot()`, `.set_runtime_notices()`, `.clear_runtime_notices()` |
| Tools | `ToolCallRouterBoundary` | `.execute_tool_call(tool_call)` |
| Turn notices | `TurnNoticeProvider` | `.consume_notices()` |
| Provider | `Provider` | `.complete(request)` |

This means you can swap any module without touching QueryLoop.

## ContextRuntime

Manages the agent's structured cognitive state:

```
ContextState
├── working_state_schema: WorkingStateSchema (field declarations)
├── working_state: dict[str, FrozenWorkingStateValue]  (current values; JSON-compatible: scalar/list/dict)
├── inherited_state: tuple[str, ...]  (from previous chapter)
├── compressed_history: list[CompressedSegment]  (summaries)
├── memory_context: str  (injected cross-session memory)
└── runtime_notices: tuple[str, ...]  (one-shot system messages)
```

The model mutates this via context protocol tools:
- `declare_schema(fields)` — declare working state fields for this chapter
- `update_state(field_name, value)` — update a field
- `extend_schema(fields)` — add fields when schema insufficient
- `start_chapter(fields?)` — start new chapter (current state → inherited)
- `recall_context(handle?, query?, limit?)` — retrieve compressed segments

## ContextRenderer

Turns ContextState into the system prompt string. Sections:

1. **Runtime Contract** — identity + security guardrails
2. **Capability Plane** — available tools summary (human-readable)
3. **Context Management Rules** — instructions for using context tools
4. **Declared Schema** — current chapter's field definitions
5. **Working State** — current field values
6. **Inherited State** — from previous chapter
7. **Compressed History** — segment summaries with handles
8. **Memory Context** — cross-session memory
9. **Runtime Notices** — one-shot messages (consumed after rendering)

## CompressionRuntime

Triggers when `len(active_messages) > budget.max_active_messages`:
1. Selects oldest messages (keeps `retain_latest_messages` most recent)
2. Feeds selected messages to `Compressor.compress()`
3. Produces `CompressedSegment(id, topic, summary)`
4. Removes compressed messages from active window
5. Adds segment to `compressed_history` in ContextState
6. If MemoryRuntime attached, indexes for recall

## Extension Points

| Want to... | Use... |
|------------|--------|
| Add a tool | `RegisteredTool` + `AgentBuilder.tools()` |
| Intercept before/after | `HookManager` + register at hook points |
| Observe events | `EventBus` + typed event subscriptions |
| Custom system prompt | `AgentBuilder.context_renderer(custom)` |
| Progressive skill disclosure | `SkillRegistry` + `register_skill_loader_tools(...)` (loads `load_skill` + `load_skill_resource`) |
| Custom compression | Implement `Compressor` protocol, pass to `.with_compression(compressor)` |
| Custom provider | Implement `Provider` protocol (just `.complete()`) |
| Custom state storage | Implement `HotSessionStore` / `DurableSessionStore` protocols |
| Custom channel | Wrap `Agent` in your own HTTP/WebSocket/gRPC handler |
| Deployment-shape assembly | `LocalRuntimeProfile`, `WebRuntimeProfile`, `DistributedWebRuntimeProfile`, or `DistributedAgentProfile` |
| Production web session lifecycle | Use `DistributedWebRuntimeProfile` with `DurableAgentSessionProvider`, `RedisSessionLeaseStore`, and `PostgresSessionSnapshotPersistence` for durable multi-node session hydration; use `DistributedWebSessionOperationsProfile` for acquire/hydrate/save/release lifecycle readiness, Redis/Postgres credentials, migration execution, lease TTL tuning, stale lease recovery policy, auth and tenant integration, and live backend verification tracking; inject a custom `AgentSessionProvider` into `WebRuntimeProfile` only for app-specific lifecycle policy |
| Agent Service Reference Layer | Use `AgentServiceReference`, `AgentServiceReferenceProfile`, and `AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS` when a web agent needs a reference service around `AsgiAgentApp composition`, `DistributedWebRuntimeProfile injection`, `auth/rate-limit hook injection`, `readiness check aggregation`, and `JSON-safe readiness evidence`; this reference service is not a platform, and gateway/TLS/CORS/WAF, tenant directory integration, Kubernetes/systemd/autoscaling, credentials, migrations, alerting, and live backend verification remain deployment-owned |
| Production readiness release gate | Use `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`, and `ReadinessEvidenceStatus` after readiness/profile/backend evidence exists; the release gate evidence bundle consumes existing readiness/profile/backend evidence, returns `blocking_checks`, `missing_required_checks`, `block_production_readiness`, `sdk_owned`, `deployment_owned`, and a JSON-safe evidence bundle, and does not execute real infrastructure checks |
| Reference state plane | Use `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, and `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` to compose `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`, `AgentServiceReference`, `DistributedWebRuntimeProfile`, and `ProductionReadinessEvidenceBundle` through readiness source aggregation and component identity evidence; this reference state plane does not create backend clients, and credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned |
| Production state-plane split | Use `ProductionStatePlaneDeploymentProfile` to keep `agent_registry`, `message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, `session_snapshot_persistence`, `state_plane_boundary_policy`, and `live_backend_verification` separate; registry/discovery may use `NacosAgentRegistryAdapter`, `NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, and `NacosRegistryEvidence` for AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering, capability-based discovery, Nacos namespace_id evidence, JSON-safe evidence, and discovery-only Nacos metadata; Nacos namespace_id is passed to register, unregister, list, resolve, and discover; Nacos is not task truth, not plan truth, not session snapshot storage, not message queue, and not worker runtime state; delivery may use `RedisAgentMessageQueue`, truth stores may use `PostgresTaskStore` and `PostgresPlanStore`, lifecycle evidence may use `WorkerProcessSupervisor`, runtime snapshots may use `SessionSnapshotPersistence`, and credentials/live backend verification remain deployment-owned |
| Worker process lifecycle evidence | Use `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and `LocalSubprocessWorkerSupervisor` for local reference start/stop/wait/heartbeat/state/evidence over `team_worker`, `planner_worker`, and `a2a_push_worker` processes; the evidence is JSON-safe lifecycle evidence with `exit_code`, timestamps including `last_heartbeat_at`, and `env_keys`; `heartbeat` records an evidence timestamp only; LocalSubprocessWorkerSupervisor does not inherit the host environment by default and WorkerProcessSpec rejects secret-like metadata keys; keep restart policy, graceful drain, Kubernetes/systemd, autoscaling, health/readiness policy, secret distribution, live backend verification, logs, and runbooks deployment-owned |
| Worker reference supervisor boundary | LocalSubprocessWorkerSupervisor is not a Kubernetes, systemd, autoscaling, or secret-distribution layer |
| Execution workspace boundary | `WorkspaceProvider` + `WorkspacePolicy`; terminal may default to local cwd, web should pass explicit workspace |
| Workspace execution isolation readiness | `WorkspaceExecutionIsolationProfile`; SDK-owned protections are `WorkspaceHandle`, `WorkspaceProvider`, `WorkspacePolicy`, scope narrowing, `WorkspaceToolSandboxPolicy`, `ToolPathSandboxRule`, path escape pre-check, and tool capability pre-check |
| Workspace backend execution | `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, and `LocalWorkspaceExecutionBackend`; use argv-only local execution and JSON-safe execution evidence with `env_keys`; the local backend does not inherit the host environment by default; Docker/E2B/enterprise runner adapters remain deployment-owned; local execution is not a production isolation boundary |
| Tool sandbox pre-checks | `WorkspaceToolSandboxPolicy` + `ToolPathSandboxRule`; keep OS/container execution isolation, filesystem mount policy, network egress policy, CPU and memory limits, secret redaction, audit logging backend, and live sandbox backend verification in the deployment/backend layer |
| Distributed task coordination | Inject `TaskStore` and `AgentMessageQueue` into `AgentCoordinator` |

## RuntimeProfile Boundary

Runtime profiles are available from `agentos.runtime.profile`:

- `LocalRuntimeProfile`
- `WebRuntimeProfile`
- `DistributedWebRuntimeProfile`
- `DistributedWebSessionOperationsProfile`
- `ProductionStatePlaneDeploymentProfile`
- `DistributedAgentProfile`

They assemble deployment collaborators and do not run turns directly.

`RuntimeProfile` owns deployment assembly:

- session identity and hydration
- workspace boundary
- persistence and memory backend selection
- event store selection
- registry/discovery strategy
- production state-plane ownership: registry is not task truth, queue is not final task or plan state, task/plan stores are not process supervisors, and session snapshots are not registry discovery metadata
- execution backend and sandbox policy
- channel capabilities
- locking, lease, cancellation, and concurrency policy

It should not move turn orchestration into the profile. The profile assembles collaborators; `QueryLoop` runs a turn.

## What AgentBuilder Does

AgentBuilder is syntactic sugar. It:
1. Creates ContextRuntime (with EventBus if provided)
2. Creates MessageRuntime
3. Creates ToolRegistry + ToolCallRouter (with context protocol tools)
4. Creates ContextRenderer (with capability plane from tools)
5. Creates ProviderRequestBuilder (renderer + messages + tool schemas)
6. Creates CompressionRuntime if requested
7. Assembles all into `Agent(query_loop_kwargs={...})`

You can skip AgentBuilder entirely and construct Agent directly if you need more control.
