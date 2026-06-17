---
name: agent-os-multi-agent
description: Reference for multi-agent coordination in agent-os  - local spawn, distributed task stores, message queues, A2A dispatch, tracing
---

# Multi-Agent Coordination

## Current Readiness

agent-os currently supports local coordination directly, distributed task primitives, Nacos registry discovery boundaries, distributed team state, team records/messages/wakeup primitives, team tools, worker session lifecycle boundaries, worker workspace/capability downgrade checks, workspace-aware tool path/capability sandbox policy, `WorkspaceExecutionIsolationProfile` readiness metadata, `WorkspaceExecutionBackend` / `SandboxBackend` workspace execution backend contracts, `LocalWorkspaceExecutionBackend` argv-only local reference execution with JSON-safe execution evidence and `env_keys`, batch worker continuation runners, daemon worker polling loops, persistent retry/backoff primitives, persistent cancellation intent primitives, team UI event stream protocol, team UI JSON replay, team UI SSE/follow, distributed team UI stream storage, and planner tools/state/template/decomposition-validation/persistent-store primitives. `NacosAgentRegistryAdapter`, `NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, and `NacosRegistryEvidence` provide AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering, capability-based discovery, Nacos namespace_id evidence, JSON-safe evidence, and discovery-only Nacos metadata. Nacos namespace_id is passed to register, unregister, list, resolve, and discover. Nacos is not task truth, not plan truth, not session snapshot storage, not message queue, and not worker runtime state; credentials and live backend verification remain deployment-owned. A2A operation exposure is fail-closed by default: `RejectAllA2AInboundAuthPolicy` is the default inbound policy, default inbound A2A operation auth is fail-closed, and A2AOperationServer default rejects unauthenticated peers before runner/store work. `AllowAllA2AInboundAuthPolicy` is explicit local/dev opt-in, local/dev peers must opt in explicitly, and `A2AOperationClient` uses an A2AOperationClient default public HTTPS egress policy unless the deployment provides a stricter allow-list policy. Public A2A services should configure `A2AOperationServer(rate_limit_policy=PeerKeyA2AOperationRateLimitPolicy(...))`; distributed/global quota storage and billing remain deployment-owned. AllowAllA2AInboundAuthPolicy is explicit local/dev opt-in. Docker/E2B/enterprise runner adapters and OS/container sandboxing for untrusted code execution remain deployment-owned.

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

Phase 97: Reference State Plane Stack adds `ReferenceStatePlaneStack`,
`ReferenceStatePlaneStackProfile`, and
`REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` as the SDK-owned reference state
plane for distributed multi-agent compositions. It connects
`NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`,
`PostgresPlanStore`, `WorkerProcessSupervisor`,
`LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`,
`PostgresSessionSnapshotPersistence`, `AgentServiceReference`,
`DistributedWebRuntimeProfile`, and `ProductionReadinessEvidenceBundle` through
readiness source aggregation and component identity evidence. It does not
create backend clients; credentials, migrations, CI matrix execution, alert
routing and runbooks remain deployment-owned.

| Form | Status |
|------|--------|
| Local spawn/dispatch | Direct |
| Cross-process task state and message delivery | Primitives ready via Postgres/Redis |
| Agent registry/discovery state plane | Primitives ready via `NacosAgentRegistryAdapter`, `NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, `NacosRegistryEvidence`, AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering, capability-based discovery, Nacos namespace_id evidence, JSON-safe evidence, and discovery-only Nacos metadata; Nacos namespace_id is passed to register, unregister, list, resolve, and discover; Nacos is not task truth, not plan truth, not session snapshot storage, not message queue, and not worker runtime state |
| Endpoint-backed remote task dispatch | Minimal internal A2A task bridge plus A2A Agent Card primitives |
| Team discussion records/messages/wakeup/tools/state/session lifecycle/permission/runner/daemon/lifecycle readiness/reference process evidence/retry/cancel/UI events | Primitives ready via `TeamRuntime`, `TeamTools`, `PostgresTeamStore`, `TeamWorkerSessionProvider`, `TeamWorkerPermissionPolicy`, `TeamWorkerRunner`, `TeamWorkerDaemon`, `WorkerProcessLifecycleDeploymentProfile`, `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `TeamWorkerRetryPolicy`, `PostgresTeamWorkerRetryStore`, `PostgresTeamWorkerCancellationStore`, `TeamUiStreamStore`, `PostgresTeamUiStreamStore`, team UI JSON replay endpoint, and team UI SSE/follow endpoint; WorkerProcessSpec rejects secret-like metadata keys |
| Planner / intent-router tools, raw LLM decomposition proposal gating, structured decomposition validation/ingestion, governance reference readiness payloads, state, assignment, failure/retry metadata, schedulable plan selection, claim-before-tick scheduler boundary, claimed scheduler daemon polling, planner scheduler governance profile, dispatch supervision payloads, stale claim sweep boundary, persistence, and orchestration readiness | Primitives ready via `PlannerRuntime`, `PlannerTools`, `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`, `PlannerRuntime.gate_decomposition_proposal`, `plan_gate_decomposition_proposal`, `PlanDecomposition`, `PlanDecompositionValidationReport`, `PlannerDecompositionPolicyDeploymentProfile`, `PlannerLlmDecompositionGovernanceProfile`, `component_refs`, `evidence_refs`, `budget_policy`, `PlanRetryPolicy`, `PlannerRuntime.schedulable_plans`, `PlannerRuntime.claimed_scheduler_tick`, `PlannerClaimedSchedulerDaemon`, `PlannerSchedulerGovernanceDeploymentProfile`, `PlannerWorkerDispatchSupervisionProfile`, `PlannerRuntime.sweep_expired_claims`, `PlannerStaleClaimSweepProfile`, `PlannerSchedulablePlan`, `PostgresPlanStore`, and `PlannerOrchestrationDeploymentProfile` |
| Team worker path/capability sandbox policy | Primitives ready via `WorkspaceToolSandboxPolicy` and `WorkspaceExecutionIsolationProfile` |
| Team worker workspace backend execution | Primitives ready via `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, and `LocalWorkspaceExecutionBackend`; the local backend does not inherit the host environment by default; local backend is not a production isolation boundary |
| Reference state plane composition | Primitives ready via `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`, readiness source aggregation, component identity evidence, and `ProductionReadinessEvidenceBundle`; it does not create backend clients |
| Full A2A interoperable agent mesh | Future extension |

Use this module for task delegation, team message boundaries, worker session registration, worker workspace/capability downgrade, worker tool path/capability sandbox policy, worker execution isolation readiness, worker process lifecycle readiness, worker continuation batches, daemon polling, retry/backoff gating, cancellation intent, UI event projection, and planner state boundaries. If the user asks for "leader + workers talking", use `TeamWorkerSessionProvider` for independent worker session lifecycle, `TeamWorkerPermissionPolicy` for workspace/capability downgrade at session creation, `WorkspaceToolSandboxPolicy` on worker `ToolCallRouter` instances for path/capability pre-execution checks, `WorkspaceExecutionIsolationProfile` to report configured `workspace_policy`, `tool_path_sandbox`, `capability_allowlist`, `execution_backend`, `process_isolation`, `resource_limits`, `network_policy`, and `audit_logging` components while keeping OS/container sandboxing, filesystem mount policy, network egress policy, CPU and memory limits, secret redaction, audit logging backend, and live sandbox backend verification deployment-owned, `WorkerProcessLifecycleDeploymentProfile` to report `worker_process_lifecycle` components (`process_supervisor`, `restart_policy`, `graceful_shutdown`, `health_probe`, `readiness_probe`, `scaling_policy`, `credential_policy`, `migration_policy`, `alerting`, `live_backend_verification`) while keeping the production process supervisor or job runner, graceful shutdown and draining, horizontal scaling policy, health/readiness endpoint wiring, alert routing and runbooks, credentials, migrations, live backend verification, and OS/container sandboxing deployment-owned, `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and `LocalSubprocessWorkerSupervisor` when a local `team_worker` needs argv-only start/stop/wait/state/evidence with JSON-safe lifecycle evidence (`exit_code`, `started_at`, `stop_requested_at`, `stopped_at`, `env_keys`) and no shell parsing, `TeamWorkerRunner` for team-message continuation processing, `TeamWorkerRetryPolicy` for failed-delivery backoff, `PostgresTeamWorkerRetryStore` for durable retry state, `PostgresTeamWorkerCancellationStore` for durable pre-run cancellation intent, `TeamUiStreamStore` for UI replay events, `PostgresTeamUiStreamStore` plus `GET /v1/teams/{team_id}/ui-events` and `GET /v1/teams/{team_id}/ui-events/stream` for multi-node UI replay/follow, and `TeamWorkerDaemon` for service-hosted polling; treat OS/container sandboxing as deployment work for tools that execute untrusted code. If the user asks for "multi-agent planner" or "intent router", use planner tools/primitives for raw LLM decomposition proposal gating through `PlannerRuntime.gate_decomposition_proposal`, `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`, and `plan_gate_decomposition_proposal`, structured decomposition validation/ingestion, dependency metadata, ready-step queries, schedulable plan selection through `PlannerRuntime.schedulable_plans`, `plan_schedulable_plans`, and `PlannerSchedulablePlan`, local and Postgres-backed plan claim/lease through `PlanClaimStore`, `PostgresPlanClaimStore`, `PlannerRuntime.claim_schedulable_plans`, and `plan_claim_schedulable_plans`, claim-before-tick scheduler boundary through `PlannerRuntime.claimed_scheduler_tick`, `plan_claimed_scheduler_tick`, `PlanClaimedSchedulerTickReport`, and `PlanClaimedSchedulerTickSkip`, claimed scheduler daemon polling through `PlannerClaimedSchedulerDaemon`, `PlannerClaimedSchedulerDaemonState`, and `PlannerClaimedSchedulerDaemonError`, planner scheduler governance profile readiness through `PlannerSchedulerGovernanceDeploymentProfile` with `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `scheduler_lock_policy`, `leader_election_policy`, `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and `live_backend_verification`, dispatch supervision payloads through `PlannerWorkerDispatchSupervisionProfile` with `claimed_scheduler_tick_loop` and `metrics_alerting` readiness metadata, stale claim sweep reports/releases through `PlanClaimSweepReport`, `PlanClaimSweepSkip`, `PlanClaimSweepStore`, `PlannerRuntime.sweep_expired_claims`, and `PlannerStaleClaimSweepProfile` with `stale_claim_sweep_schedule` and `sweep_safety_window` readiness metadata, failure recording, retryable-step queries, retry reset, state/templates/assignments, `PlannerSchedulerDaemon` for service-hosted polling over explicitly supplied plan ids, `PostgresPlanStore` when plans must survive restarts or cross nodes, `docs/migrations/2026-06-16-postgres-plan-claims.sql` when plan claims must survive restarts or coordinate scheduler workers across nodes, `PlannerDecompositionPolicyDeploymentProfile` to expose readiness metadata for `prompt_policy`, `output_schema`, `validation_gate`, `template_mapping_policy`, `approval_policy`, `model_routing_policy`, `evaluation_policy`, `trace_logging`, and `rollback_policy`, `PlannerLlmDecompositionGovernanceProfile` to expose governance reference readiness payloads with `component_refs`, `evidence_refs`, and `budget_policy` while keeping prompt text and prompt review workflow deployment-owned, `PlannerOrchestrationDeploymentProfile` to expose readiness metadata for `decomposition_policy`, `dag_scheduler`, `worker_dispatch_loop`, `compensation_policy`, `plan_store`, and `worker_supervision`, `WorkerProcessLifecycleDeploymentProfile` when planner worker or scheduler processes need lifecycle readiness metadata, `WorkerProcessSpec`/`WorkerProcessState`/`WorkerProcessSupervisor`/`LocalSubprocessWorkerSupervisor` when a local `planner_worker` needs reference subprocess lifecycle evidence, and `WorkspaceExecutionIsolationProfile` when generated subagent work needs an execution isolation contract, while keeping automatic LLM decomposition policy execution, including LLM prompt/model/approval/evaluation policy, plan discovery tenant routing, global fairness, distributed scheduler locks, stale claim sweep scheduling policy, stale lease recovery policy, production process supervision, worker dispatch loop execution, complex compensation orchestration, lifecycle execution, live backend verification, Kubernetes, systemd, autoscaling, secret distribution, and real sandbox enforcement as app-owned work.

## Runtime Boundaries

`AgentCoordinator` orchestrates multi-agent work. It depends on protocol-style boundaries:

| Boundary | Responsibility | In-memory adapter | Production adapter |
|----------|----------------|-------------------|--------------------|
| `TaskStore` | Task/result truth source, lease, cancel intent, late result | `TaskTable` | `PostgresTaskStore` |
| `AgentMessageQueue` | Envelope delivery/notification | `AgentInbox` | `RedisAgentMessageQueue` |
| `TeamStore` | Team/member/message truth source | `InMemoryTeamStore` | `PostgresTeamStore` |

Use `TaskTable` + `AgentInbox` for local tests and single-process development. Use `PostgresTaskStore` + `RedisAgentMessageQueue` when task state and delivery must cross processes. Use `PostgresTeamStore` when team records, members, and messages must be visible to multiple nodes.

Source: `src/agentos/multi/coordinator.py`, `src/agentos/multi/task_store.py`, `src/agentos/multi/message_queue.py`, `src/agentos/multi/tasks.py`, `src/agentos/multi/inbox.py`, `src/agentos/multi/postgres_tasks.py`, `src/agentos/multi/redis_queue.py`.

## Local Coordinator

```python
from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskTable,
)

coordinator = AgentCoordinator(
    registry=InMemoryRegistry(),
    task_store=TaskTable(),
    message_queue=AgentInbox(),
    spawn_executor=SpawnExecutor(max_workers=4),
    subagent_factory=subagent_factory,
)

coordinator.attach_agent(
    AgentCard(
        agent_id="parent",
        name="Parent",
        description="Coordinates work.",
        capabilities=("coordinate",),
    ),
    parent_agent,
)
```

Source: `tests/multi/test_coordinator_distributed_boundaries.py`, `tests/multi/test_coordinator_spawn.py`.

## Dispatch To A Local Expert

Register an expert agent with capabilities, then dispatch by required capability:

```python
coordinator.attach_agent(
    AgentCard(
        agent_id="expert",
        name="Expert",
        description="Reviews Python code.",
        capabilities=("code-review", "python"),
        max_concurrent_tasks=1,
    ),
    expert_agent,
)

handle = coordinator.dispatch(
    instruction="Review this Python module.",
    required_capabilities=("code-review",),
    parent_agent_id="parent",
)

deliveries = coordinator.inbox.collect("expert")
for delivery in deliveries:
    coordinator.execute_expert_envelope(delivery.envelope)
    coordinator.inbox.ack("expert", delivery.delivery_id)

results = coordinator.collect_results("parent")
```

Source: `tests/multi/test_coordinator_dispatch.py`, `src/agentos/multi/expert.py`.

## Distributed Adapters

```python
from agentos.multi import AgentCoordinator, SpawnExecutor
from agentos.multi.postgres_tasks import PostgresTaskStore
from agentos.multi.redis_queue import RedisAgentMessageQueue

coordinator = AgentCoordinator(
    registry=registry,
    task_store=PostgresTaskStore(dsn="postgresql://user:pass@host/db"),
    message_queue=RedisAgentMessageQueue(
        url="redis://host:6379/0",
        allowed_consumer_agent_ids=("worker-1", "worker-2"),
    ),
    spawn_executor=SpawnExecutor(max_workers=4),
    subagent_factory=subagent_factory,
)
```

Run `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql` before using `PostgresTaskStore`. Optional dependencies are required at runtime: `agentos[postgres]` for Postgres and `agentos[redis]` for Redis.
Production Redis consumers must declare `allowed_consumer_agent_ids`; local demos can explicitly opt into `allow_unscoped_consumers=True`.

Source: `src/agentos/multi/postgres_tasks.py`, `src/agentos/multi/redis_queue.py`, `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`, `tests/multi/test_optional_adapters.py`.

## Cancellation Semantics

- Queued tasks can become terminal `cancelled` immediately.
- Running tasks receive `cancel_requested_at`; the worker must ack cancellation or the task converges through timeout/late-result handling.
- Terminal writes from claimed workers should include matching `worker_id` and `attempt` when using claim/lease flows.

Source: `src/agentos/multi/tasks.py`, `src/agentos/multi/coordinator.py`, `tests/multi/test_task_store_contract.py`, `tests/multi/test_coordinator_distributed_boundaries.py`.

## Remote A2A Dispatch

Endpoint-backed agents use `RemoteTaskExecutor` + `A2AAdapter` HTTP dispatch. This is separate from Redis queue delivery.

Current A2A boundary:

- outbound: `A2AAdapter.send_task(card, request)`
- inbound: `A2AServerAdapter.handle_task(payload, headers)`
- ASGI routes: `/a2a/tasks`, `/a2a/health`
- payload model: agent-os `TaskRequest` / `TaskResult`

This is an internal JSON task bridge, not full A2A protocol compliance.

External A2A protocol boundary:

- discovery: `A2AAgentCard`, `A2ACardResolver`, `/.well-known/agent-card.json`
- operation client/server: `A2AOperationClient`, `A2AOperationServer`
- protocol versioning: `A2AProtocolVersionPolicy` sends `A2A-Version` and
  rejects unsupported inbound versions with A2A `-32009`
- text parts: `A2AMessagePart` serializes to the A2A 1.0 `{"text": "..."}`
  shape while still accepting the legacy `{"kind": "text", "text": "..."}`
  shape on inbound requests
- file/data parts: `A2AMessagePart.from_file_bytes(...)`,
  `A2AMessagePart.from_file_url(...)`, and `A2AMessagePart.from_data(...)`
  serialize to A2A 1.0 `raw`/`url`/`data` wrapper shapes while still accepting
  legacy `kind: file` and `kind: data` inbound requests
- artifacts/events: `A2AArtifact` serializes task artifacts as
  `artifactId` plus part wrappers; task status and artifact update events use
  A2A 1.0 `statusUpdate` and `artifactUpdate` wrapper shapes while accepting
  legacy status-update input
- extension negotiation: `A2AExtensionNegotiationPolicy` emits and parses
  `A2A-Extensions`, rejects missing required extensions with A2A `-32008`,
  and lets optional unsupported extensions degrade by default
- JWT/OIDC claims auth: `OidcClaimsA2AInboundAuthPolicy` with
  `HmacA2AJwtVerifier` validates issuer, audience, time validity, and optional
  peer ids for inbound A2A operation calls through the existing auth boundary
- OIDC discovery metadata: `OidcDiscoveryMetadataProvider` fetches
  `/.well-known/openid-configuration`, requires exact issuer match, requires
  HTTPS `jwks_uri`, caches metadata, and exposes the JWKS URI for later verifier
  rollout
- RS256/JWKS JWT verification: `JwksA2AJwtVerifier` uses configured HTTPS JWKS
  URLs or `OidcDiscoveryMetadataProvider`, enforces `alg == RS256`, verifies RSA
  SHA-256 signatures, and returns the shared `A2AJwtClaims` projection; install
  the optional `security` extra for this path
- self-conformance: `A2AConformanceHarness` produces a structured SDK report
  across Agent Card fields, JSON-RPC envelopes, version headers, extension
  negotiation, message parts, artifacts, event wrapper shapes,
  `message/stream request` envelopes, and typed `message stream event` payloads
- per-peer rate limit: `A2AOperationRateLimitPolicy` and
  `PeerKeyA2AOperationRateLimitPolicy` throttle authenticated operation calls
  by peer, operation, task, and resource context before runner/store work; use
  `A2APeerIdResolver` to derive the peer id and expect denied calls to surface
  `A2ARateLimitError` as the A2A `-32029` `rate limit exceeded` error
- current operation routes: `/a2a/message:send`, `/a2a/message:stream`, `/a2a/tasks/{id}`, `/a2a/tasks/{id}:cancel`, and `tasks/resubscribe`
- outbound message stream initiation: `A2AOperationClient.stream_message(...)`
  posts `message/stream` through the same protocol version, extension
  negotiation, auth provider, and egress URL policy boundary as
  `send_message(...)`
- outbound message stream event consumption:
  `A2AOperationClient.stream_message_events(...)` consumes peer
  `text/event-stream` responses as `A2AMessageStreamEvent` values through
  `parse_a2a_sse_events(...)`; reconnect, backpressure, durable stream
  cursors, and fan-out remain transport/deployment-owned
- outbound task resubscribe:
  `A2AOperationClient.task_resubscribe(...)` posts one-shot cursor
  `tasks/resubscribe` requests through the same Agent Card URL, protocol
  version, extension negotiation, auth provider, trace propagation, and egress
  URL policy boundary as message operations; automatic reconnect loops,
  durable cursor storage, fan-out, backpressure, gateway quota, billing, and
  credential issuance remain deployment-owned
- stream lifecycle readiness:
  `A2AStreamLifecycleDeploymentProfile` exposes JSON-safe readiness metadata
  for configured stream lifecycle components while keeping automatic reconnect
  loops, durable cursor storage, fan-out, backpressure, gateway quota, billing,
  credential issuance, process supervision, DNS pinning, enterprise egress
  proxy, CA rollout, tenant directory lifecycle, and external conformance
  execution deployment-owned
- external conformance readiness:
  `A2AExternalConformanceExecutionProfile` gates imported external conformance
  reports against required checks (`agent-card`, `message-send`,
  `message-stream`, `tasks-resubscribe`, `push-notification-config`) and
  configured components (`external_suite_runner`, `target_endpoint`,
  `credential_policy`, `network_egress_policy`, `version_matrix`,
  `ci_artifact_retention`, `failure_alerting`) without making a certification
  claim. `A2AExternalConformanceInvocationPlan` captures external suite
  invocation planning before CI runs the deployment-owned suite, including suite
  id/version, target, command, required checks, credential policy reference,
  network egress policy reference, version matrix reference, artifact retention
  reference, and failure alerting reference.
  `A2AExternalConformanceInvocationGateReport` turns that plan into an external
  conformance invocation gate report with no certification claim.
  `A2AExternalConformanceRunner` is the narrow SDK protocol for invoking that
  plan, and `A2AExternalConformanceCliRunner` is the external conformance CLI
  runner reference adapter: it executes argv-only commands with no shell
  parsing, imports a report path or stdout JSON, captures bounded stdout/stderr
  summaries, records timeout/nonzero/import-error evidence, and exposes
  `env_keys` without retaining secret values.
  `A2AExternalConformanceExecutionRecord` captures already-executed
  suite command, target, exit code, timestamps, summaries, artifact URI, and
  imported report evidence as an external conformance execution record.
  `A2AExternalConformanceGateReport` turns that evidence into an external
  conformance gate report for local release/readiness policy with no
  certification claim; official suite selection/installation, CI automation,
  artifact storage, live backend verification, and certification attestation
  remain deployment-owned
- current message stream route: `POST /a2a/message:stream` emits an initial task SSE event after the message stream operation boundary and then follows task updates through `tasks/resubscribe` when a task lifecycle runner is configured
- current subscribe route: `POST /a2a/tasks/{id}:subscribe` with SSE `task_status_update` events through the task subscribe operation boundary
- payload model: `A2AMessage`, `A2ATask`, `A2ATaskSubscriptionEvent`, `A2AMessageStreamEvent`, `A2AOperationRequest`, `A2AOperationResponse`
- internal task projection: `TaskStoreA2ATaskLifecycleRunner`

This starts protocol operation parity for `message/send`, `message/stream`,
task get/cancel, and task subscribe/status updates, but does not make
`/a2a/tasks` a full A2A endpoint.

Remaining A2A gaps are automatic reconnect loops, durable cursor storage, fan-out, backpressure, gateway quota, billing, credential issuance, process supervision, DNS pinning, enterprise egress proxy, CA rollout, tenant directory lifecycle, official external suite selection/installation, CI matrix execution, and certification attestation governance. SDK-level Agent Card skill media metadata, `supportedInterfaces`, capability extensions, protocol version negotiation, extension negotiation, SDK self-conformance reports, external conformance result import, external conformance invocation planning via `A2AExternalConformanceInvocationPlan` and `A2AExternalConformanceInvocationGateReport`, external conformance CLI runner reference execution via `A2AExternalConformanceRunner` and `A2AExternalConformanceCliRunner`, external conformance execution readiness via `A2AExternalConformanceExecutionProfile`, external conformance execution record evidence via `A2AExternalConformanceExecutionRecord`, external conformance gate report evidence via `A2AExternalConformanceGateReport`, JWT/OIDC claims validation through shared-secret or RS256/JWKS verifiers, OIDC discovery metadata fetch/validation/cache through `OidcDiscoveryMetadataProvider`, RS256/JWKS JWT verification through `JwksA2AJwtVerifier`, text/file/data part 1.0 payload shapes, artifact/status-update/artifact-update payload wrappers, signed-card/trust primitives, local HMAC card key rotation primitives, bearer credential rotation primitives (`A2ABearerCredential`, `RotatingBearerA2ACredentialStore`, `RotatingBearerA2AAuthProvider`, `RotatingBearerA2AInboundAuthPolicy`), JWKS `oct`/HS256 key discovery through explicitly configured HTTPS URLs, outbound peer-auth header injection, inbound peer-auth policy enforcement, inbound peer allow-list policy, operation allow-list policy, resource allow-list policy, claims-backed tenant RBAC policy, per-peer rate limit primitives (`A2AOperationRateLimitPolicy`, `PeerKeyA2AOperationRateLimitPolicy`, `A2APeerIdResolver`, `A2ARateLimitError`), `A2AStreamLifecycleDeploymentProfile`, push notification config routes, configurable webhook URL allow-list policy, one-shot webhook delivery, local enqueue/claim/retry/dead-letter worker primitives, `A2APushNotificationDaemon`, and Postgres-backed push config/delivery stores are available for deployments that need a local trust boundary.
credential issuance and secret distribution, KMS/secret-manager governance, and rollout audit controls remain deployment-owned.

Source: `src/agentos/multi/remote.py`, `src/agentos/channels/a2a.py`, `src/agentos/channels/a2a_operations.py`, `tests/multi/test_remote_dispatch.py`, `tests/channels/test_a2a_operations.py`.

## Team Runtime

`TeamRuntime` stores team records, members, and messages in `TeamStore`, then uses `AgentMessageQueue` only as a wakeup hint. `messages_for()` reads from the store and does not drain task envelopes. `TeamTools` exposes `team_create`, `agent_create`, `team_say`, `team_read_messages`, and `team_delete` as normal external tools scoped to the calling `owner_agent_id`.

Use `InMemoryTeamStore` for local tests and single-process development. Use `PostgresTeamStore` for production clusters that need team state shared across nodes, and run `docs/migrations/2026-06-12-postgres-team-store.sql` before deployment.

Use `TeamWorkerSessionProvider` when `agent_create` should register independent worker sessions. `InMemoryTeamWorkerSessionProvider` is for local tests and prototypes; production deployments should provide a provider that maps workers to the app's durable session system. Use `TeamWorkerPermissionPolicy` at the session provider boundary to reject worker workspaces that broaden scope, escape the team workspace root, or request capabilities outside an allow-list. Production `agent_create` specs must configure `TeamWorkerPermissionPolicy(allowed_capabilities=...)` and attach `WorkspaceToolSandboxPolicy` to worker routers before external handlers run. Use `WorkspaceExecutionIsolationProfile` when deployment probes or specs must state whether workspace isolation components are configured; SDK-owned protections stop at path escape pre-check, tool capability pre-check, local reference execution, and JSON-safe execution evidence. Use `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, and `WorkspaceExecutionPolicy` for a swappable Local/Docker/E2B/enterprise runner boundary. Use `LocalWorkspaceExecutionBackend` only as the argv-only local reference backend; it emits `env_keys` and is not a production isolation boundary.

Use `TeamWorkerRunner` to process pending team-message deliveries for worker sessions and call worker `run_continuation()` through an app-provided agent resolver. Add `TeamWorkerRetryPolicy` and a `TeamWorkerRetryStore` when failed deliveries should be retried with backoff instead of hot-looping. Use `PostgresTeamWorkerRetryStore` when retry state must survive worker restarts or be inspected across nodes. Use `TeamWorkerCancellationStore` when queued or retry-delayed deliveries need to be skipped before execution, and use `PostgresTeamWorkerCancellationStore` when cancellation intent must survive worker restarts or be inspected across nodes. Inject `TeamUiStreamStore` into `TeamRuntime` and `TeamWorkerRunner` when a UI needs replayable team lifecycle, message, worker result, retry, and cancellation events. Use `PostgresTeamUiStreamStore` plus the ASGI team UI replay and follow endpoints when UI history must cross nodes. Use `TeamWorkerDaemon` when a service needs a start/stop/join polling loop around that runner. Use `WorkerProcessLifecycleDeploymentProfile` when the service needs JSON-safe readiness metadata for `process_supervisor`, `restart_policy`, `graceful_shutdown`, `health_probe`, `readiness_probe`, `scaling_policy`, `credential_policy`, `migration_policy`, `alerting`, and `live_backend_verification`; the profile reports readiness but does not launch, restart, drain, scale, credential, migrate, or verify real worker processes. Use `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and `LocalSubprocessWorkerSupervisor` when a local worker service needs SDK-owned start/stop/wait evidence for `team_worker`, `planner_worker`, or `a2a_push_worker` processes. The reference adapter is argv-only, uses no shell parsing, emits JSON-safe lifecycle evidence with `exit_code`, `started_at`, `stop_requested_at`, `stopped_at`, and `env_keys`, and is not a Kubernetes, systemd, autoscaling, or secret-distribution layer. Pass narrowed `WorkspaceHandle` values to workers and attach `WorkspaceToolSandboxPolicy` to worker tool routers when path/capability pre-execution checks are required.

## Planner Runtime

`PlannerRuntime` stores `PlanState`, `PlanStep`, `PlanDecomposition`, `PlanStepSpec`, `SubAgentTemplate`, `PlanRetryPolicy`, and `EvidenceHandle` values in `PlanStore`. `PlannerRuntime.gate_decomposition_proposal(...)` accepts a raw LLM/main-agent JSON-like proposal, applies `PlanDecompositionGatePolicy`, reuses validation, and returns a JSON-safe `PlanDecompositionGateReport` without mutating `PlanStore`. `PlannerRuntime.validate_decomposition(...)` returns a JSON-safe `PlanDecompositionValidationReport` without mutating `PlanStore`, so app-owned LLM planners can validate objective, step, template, dependency, duplicate-id, and cycle constraints before calling `create_plan_from_decomposition(...)`. `PlannerRuntime.gate_llm_governance_evidence(...)` consumes `PlannerLlmGovernanceEvidenceRecord` values and returns a `PlannerLlmGovernanceEvidenceGateReport` as planner LLM governance execution evidence; this per-proposal governance evidence gate blocks production plan creation when prompt/model/approval/evaluation/validation evidence is missing or failed, while deployment-owned prompt/model/approval/evaluation/validation execution owns the actual prompt run, model router, approval workflow, evaluation suite, schema validator, artifact retention, and certification decision. `PlannerRuntime.schedulable_plans(...)` returns JSON-safe `PlannerSchedulablePlan` summaries for owner/status-filtered plans that currently have ready pending steps, due retryable failed steps, or pending-dispatch assignment recovery work. `PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`, `PlanClaimRecord`, `PlannerRuntime.claim_schedulable_plans(...)`, and `plan_claim_schedulable_plans` provide local and Postgres-backed SDK-owned plan claim/lease boundaries with JSON-safe `claimed`/`busy` results. `PlannerRuntime.claimed_scheduler_tick(...)` and `plan_claimed_scheduler_tick` provide a claim-before-tick scheduler boundary that ticks only plans claimed by the current worker and returns `PlanClaimedSchedulerTickReport` plus `PlanClaimedSchedulerTickSkip` records. `PlannerClaimedSchedulerDaemon` hosts claimed scheduler daemon polling over that boundary and exposes `PlannerClaimedSchedulerDaemonState` plus `PlannerClaimedSchedulerDaemonError` records. `PlannerSchedulerGovernanceDeploymentProfile` exposes planner scheduler governance profile readiness and scheduler governance readiness metadata for `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `scheduler_lock_policy`, `leader_election_policy`, `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and `live_backend_verification`. `PlannerWorkerDispatchSupervisionProfile` consumes those report histories and returns JSON-safe health/readiness dispatch supervision payloads. `PlannerRuntime.sweep_expired_claims(...)` uses `PlanClaimSweepStore` to report expired `PlanClaimRecord` values, supports dry-run, releases only exact inspected claims, and returns `PlanClaimSweepReport` / `PlanClaimSweepSkip` payloads; `PlannerStaleClaimSweepProfile` summarizes recent sweep reports for readiness probes. `PlannerTools` exposes create/gate-decomposition-proposal/create-from-decomposition/add/assign/ready/schedulable/claim-schedulable/claimed-scheduler-tick/dispatch/scheduler-tick/fail/retryable/retry/evidence/complete/status operations as normal external tools, including `plan_gate_decomposition_proposal` for raw proposal gating and `plan_schedulable_plans` for schedulable plan selection. Planner assignments carry `dispatch_status`, `submitted_at`, and `dispatch_error` as dispatch outbox evidence; `PlannerRuntime.dispatch_ready_steps(...)` recovers pending assignments before new dispatch, and `PlannerRuntime.recover_pending_dispatches(...)` can be called during startup or repair runs with the original `task_id` as the idempotency key. Coordinators should return an existing task or raise `PlanDispatchAlreadySubmittedError` / `TaskAlreadySubmittedError` when that `task_id` was already accepted; planner recovery treats that typed duplicate signal as submitted evidence. This is at-least-once recovery evidence, not a distributed exactly-once guarantee. `PlannerSchedulerDaemon` wraps `PlannerRuntime.scheduler_tick(...)` in a small service-hosted polling loop for explicitly supplied plan ids and exposes `PlannerSchedulerDaemonState` for status, reports, errors, and timestamps. `PlannerDecompositionPolicyDeploymentProfile` exposes JSON-safe readiness metadata for `prompt_policy`, `output_schema`, `validation_gate`, `template_mapping_policy`, `approval_policy`, `model_routing_policy`, `evaluation_policy`, `trace_logging`, and `rollback_policy`. `PlannerLlmDecompositionGovernanceProfile` exposes governance reference readiness payloads through `component_refs`, `evidence_refs`, and `budget_policy` without storing prompt text and prompt review workflow details. `PlannerOrchestrationDeploymentProfile` exposes JSON-safe readiness metadata for `decomposition_policy`, `dag_scheduler`, `worker_dispatch_loop`, `compensation_policy`, `plan_store`, and `worker_supervision`. Assignment delegates execution through `AgentCoordinator` but does not mutate `QueryLoop` or share parent active messages with workers.

Use planner tools/primitives for intent-router and plan-and-execute patterns. Use `PlannerRuntime.gate_decomposition_proposal(...)` or `plan_gate_decomposition_proposal` before plan creation when a leader agent or app-owned LLM planner has emitted a raw JSON-like proposal. Use `PlannerRuntime.validate_decomposition(...)` before `plan_create_from_decomposition` when a leader agent or app-owned planner has emitted a structured plan proposal. Use `PlannerLlmGovernanceEvidenceRecord`, `PlannerRuntime.gate_llm_governance_evidence`, and `PlannerLlmGovernanceEvidenceGateReport` when a production planner needs a per-proposal governance evidence gate after deployment-owned prompt/model/approval/evaluation/validation execution and before `plan_create_from_decomposition`; reject reports with `block_plan_creation=True`. Use `PlanStep.depends_on` and `plan_ready_steps` when an app-owned scheduler should dispatch only dependency-ready work. Use `PlannerRuntime.schedulable_plans(...)` or `plan_schedulable_plans` when a deployment-owned scheduler needs schedulable plan selection; the SDK returns `PlannerSchedulablePlan` summaries. Use `PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`, `PlannerRuntime.claim_schedulable_plans(...)`, or `plan_claim_schedulable_plans` when a scheduler worker needs a plan claim/lease boundary after selection. Use `PlannerRuntime.claimed_scheduler_tick(...)` or `plan_claimed_scheduler_tick` when a scheduler worker should claim schedulable plans and tick only its own `claimed` plans; the report uses `PlanClaimedSchedulerTickReport` and `PlanClaimedSchedulerTickSkip` to show claims, busy skips, tick reports, and optional release results. Use `PlannerClaimedSchedulerDaemon` when a supervised service needs claimed scheduler daemon polling through `PlannerRuntime.claimed_scheduler_tick(...)`; `PlannerClaimedSchedulerDaemonState` records worker id, lease, owner/status filters, limits, release policy, iterations, reports, errors, and timestamps. Use `PlannerSchedulerGovernanceDeploymentProfile` when a production scheduler needs a planner scheduler governance profile for `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `scheduler_lock_policy`, `leader_election_policy`, `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and `live_backend_verification` readiness metadata while real routing, fairness queues, distributed locks, leader election, stale recovery, worker dispatch execution, compensation, credentials, migrations, alerting, and live backend verification remain deployment-owned. Use `PlannerWorkerDispatchSupervisionProfile` when a supervisor or readiness endpoint needs a planner worker dispatch supervision profile over recent reports; it emits a dispatch supervision payload with `claimed_scheduler_tick_loop`, `metrics_alerting`, claim, busy, tick-failed, release, and consecutive-failure metadata. Use `PlannerRuntime.sweep_expired_claims(...)` when a deployment-owned cron, scheduler, or operator task needs a stale claim sweep boundary; it returns `PlanClaimSweepReport`, supports dry-run, and releases only exact expired `PlanClaimRecord` values through `PlanClaimSweepStore`. Use `PlannerStaleClaimSweepProfile` when readiness probes need a planner stale claim sweep profile with `stale_claim_sweep_schedule`, `sweep_safety_window`, `plan_claim_store`, `scheduler_lock_policy`, `metrics_alerting`, and `live_backend_verification` metadata. Use `docs/migrations/2026-06-16-postgres-plan-claims.sql` when Postgres-backed claim state must survive restarts or coordinate scheduler workers across nodes. Use `PlanRetryPolicy`, `plan_fail_step`, `plan_retryable_steps`, and `plan_retry_step` when a worker or scheduler needs auditable failure recovery with attempt counts and backoff metadata. Use `plan_scheduler_tick` or `PlannerRuntime.scheduler_tick(...)` when a deployment-owned daemon, cron job, or scheduler agent needs one SDK-owned one-shot scheduler tick that resets due retryable steps and then performs bounded ready-step dispatch, returning a `PlanSchedulerTickReport`. Use `PlannerSchedulerDaemon` when a supervised service process needs a small SDK-owned loop that repeatedly calls `PlannerRuntime.scheduler_tick(...)` for explicitly supplied plan ids and records `PlannerSchedulerDaemonState`. Use `PostgresPlanStore` plus `docs/migrations/2026-06-15-postgres-plan-store.sql` when plan state must survive restarts or be visible across nodes. Use `PlannerDecompositionPolicyDeploymentProfile` when deployment probes or specs need to state which automatic decomposition components are configured: `prompt_policy`, `output_schema`, `validation_gate`, `template_mapping_policy`, `approval_policy`, `model_routing_policy`, `evaluation_policy`, `trace_logging`, and `rollback_policy`. Use `PlannerLlmDecompositionGovernanceProfile` when deployment probes or specs need traceable governance reference readiness payloads: configure `component_refs` and `evidence_refs` for `prompt_policy`, `model_routing_policy`, `approval_policy`, `evaluation_policy`, `trace_logging`, `rollback_policy`, `output_schema`, `validation_gate`, `template_mapping_policy`, `budget_policy`, and `live_backend_verification` while keeping prompt text and prompt review workflow, model router implementation, approval workflow, eval execution, rollout, rollback, and live backend checks deployment-owned. Use `PlannerOrchestrationDeploymentProfile` when deployment probes or specs need to state which orchestration components are configured: `decomposition_policy`, `dag_scheduler`, `worker_dispatch_loop`, `compensation_policy`, `plan_store`, and `worker_supervision`. Use `WorkerProcessLifecycleDeploymentProfile` when planner worker or scheduler services need `worker_process_lifecycle` readiness metadata for `process_supervisor`, `restart_policy`, `graceful_shutdown`, `health_probe`, `readiness_probe`, `scaling_policy`, `credential_policy`, `migration_policy`, `alerting`, and `live_backend_verification`. Use `WorkspaceExecutionIsolationProfile` when planner-generated subagent execution needs workspace isolation readiness metadata, and use `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, and `LocalWorkspaceExecutionBackend` when generated subagent work needs a swappable Local/Docker/E2B/enterprise runner boundary with argv-only local reference execution and JSON-safe execution evidence. When the model needs plan awareness in working state, write only the compact output of `plan_to_working_state_summary(plan)`; do not store full `PlanState`, task ids, workspace paths, or evidence URIs in working state. Automatic LLM decomposition policy execution, including LLM prompt/model/approval/evaluation policy execution and deployment-owned prompt/model/approval/evaluation/validation execution, real plan discovery tenant routing implementation, global fairness queues, distributed scheduler locks, stale claim sweep scheduling policy, process supervision, worker dispatch loop execution, compensation orchestration, worker process lifecycle execution, live backend verification implementation, Docker/E2B/enterprise runner adapter implementation, and OS/container sandboxing remain app-owned.

## Production Recovery Helpers

- `OutboxReconciler` scans task outbox rows and resends terminal task result envelopes.
- `RedisAgentMessageQueue.reclaim_pending()` reclaims idle Redis Stream pending messages and moves exhausted messages to a dead-letter stream.
- `RedisContinuationTrigger` publishes task completion notices through Redis Pub/Sub and can fall back to TaskStore polling.
- Live Redis/Postgres tests live under `tests/integration/` and are skipped unless `AGENTOS_RUN_INTEGRATION=1` is set with `docker-compose.test.yml` services.

Source: `src/agentos/multi/reconciler.py`, `src/agentos/multi/redis_queue.py`, `src/agentos/multi/redis_continuation.py`, `tests/multi/test_outbox_reconciler.py`, `tests/multi/test_redis_pending_retry.py`, `tests/multi/test_redis_continuation.py`, `tests/integration/test_live_backends.py`, `docker-compose.test.yml`.

## Team Discussion Remaining Gaps

Team records/messages/wakeup primitives now exist, but production team discussion needs more than Phase 5A:

- shared artifact/evidence handles with explicit workspace policy
- OS/container sandboxing for untrusted code execution

Current `AgentCoordinator` intentionally keeps subagents isolated: workers do not share the parent active messages and do not directly mutate the parent working state. Parent agents only receive task results and must explicitly absorb findings into their own state.

Worker sessions should receive narrowed `WorkspaceHandle` values. `TeamWorkerPermissionPolicy` enforces this at the worker session provider boundary when the app passes team and worker workspace handles. Team runtime must pass artifact/evidence handles explicitly rather than sharing parent active messages or broad filesystem roots.

Roadmap Phase 9A adds team tools on top of the existing task/message queue primitives. Phase 12A adds `PostgresTeamStore` for distributed team state. Phase 13A adds `TeamWorkerSessionProvider` for worker session lifecycle. Phase 14A adds `TeamWorkerRunner` for batch worker continuation execution. Phase 15A adds `TeamWorkerDaemon` for service-hosted polling. Phase 16A adds retry/backoff primitives. Phase 16B adds `PostgresTeamWorkerRetryStore` for persistent retry state. Phase 17A adds cancellation intent primitives. Phase 18A adds worker workspace/capability downgrade checks. Phase 19A adds `PostgresTeamWorkerCancellationStore` for persistent cancellation state. Phase 20A adds `TeamUiStreamStore` for UI event replay. Phase 20B adds `PostgresTeamUiStreamStore` and JSON replay endpoint. Phase 20C adds team UI SSE/follow endpoint. Phase 21A adds workspace-aware tool path/capability sandbox policy. Phase 88 adds `WorkspaceExecutionBackend` / `SandboxBackend` plus `LocalWorkspaceExecutionBackend` local reference execution. Remaining work is Docker/E2B/enterprise runner adapter implementation and OS/container sandboxing for untrusted code execution.
