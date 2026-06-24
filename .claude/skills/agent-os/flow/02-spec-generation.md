---
name: agent-os-spec-generation
description: Generate agent-spec.yaml from confirmed requirements; produces a blueprint that implementation workers can follow independently.
---

# Spec Generation

## Prerequisites

The user has confirmed the 6-dimension summary from `flow/01-requirements.md`.

## Output

Generate `specs/agent-spec.yaml` in the target project directory. This file is the single source of truth for implementation.

## Spec Schema

```yaml
version: 1
name: <agent-name>
description: <one-line purpose>

provider:
  type: AnthropicProvider | OpenAIProvider | OpenAICompatibleProvider
  model: <model-id>
  max_tokens: <number>
  base_url: <only for OpenAICompatibleProvider>

deployment:
  mode: local | single-node | multi-node
  readiness: direct | primitives-ready
  runtime_profile: LocalRuntimeProfile | WebRuntimeProfile | DistributedWebRuntimeProfile | DistributedAgentProfile
  production_design_constraints:
    phase: "Phase 99: SDK Skill / Spec Generator Finalization"
    purpose: spec generator finalization
    role: production agent design constraint generator
    template: SDK-owned constraint template
    must_explicitly_choose: true
    agent_form: terminal agent | single-node web agent | distributed web agent | team/planner/A2A primitive | custom
    runtime_profile: LocalRuntimeProfile | WebRuntimeProfile | DistributedWebRuntimeProfile | DistributedAgentProfile
    state_plane_components: [agent_registry, message_queue, task_store, plan_store, worker_process_supervisor, session_snapshot_persistence, state_plane_boundary_policy, live_backend_verification]
    persistence_backend: memory | sqlite | filesystem | postgres | custom
    registry_backend: NacosAgentRegistryAdapter | PersistentAgentRegistry | static | custom | none
    queue_backend: RedisAgentMessageQueue | AgentInbox | custom | none
    worker_supervisor: WorkerProcessSupervisor | LocalSubprocessWorkerSupervisor | deployment-owned | custom | none
    a2a_exposure: none | internal-a2a-task-bridge | discovery | operation-client | operation-server
    planner_team_mode: single | local-spawn | distributed-task | team-primitives | planner-primitives
    production_readiness_checklist: agentos.readiness required_app_glue plus release gate evidence
    sandbox_posture: trusted tools only | deployment-owned isolation | future adapter
    infrastructure_creation: does not create deployment-owned infrastructure
  release_scope:
    phase: "Phase 96: Release Scope Re-baseline"
    reference: docs/release-scope.md
    summary: release scope re-baseline for the first production SDK release
    supports: [trusted tools, internal service orchestration, terminal agent, single-node web agent, distributed web agent, team/planner/A2A primitive, production state plane, readiness evidence, audit evidence]
    sandbox_adapter: Sandbox / Docker / E2B / microVM / enterprise runner adapter
    sandbox_status: non-blocking future adapter
    release_blocker: not a release blocker
    untrusted_code: does not promise physical isolation for untrusted code execution
    sdk_boundaries: [WorkspaceExecutionBackend, SandboxBackend, LocalWorkspaceExecutionBackend, policy/capability/path pre-check, audit evidence]
    sandbox_posture: trusted tools only | deployment-owned isolation | future adapter
  release_hardening:
    phase: "Phase 100: Release Hardening"
    gate: release hardening gate
    evidence: release candidate evidence
    public_api_audit: tests/architecture/test_public_api.py
    api_stability: docs/api-stability.md
    stable_api: required
    experimental_api: classified
    migration_index: docs/migrations/README.md
    docs_alignment: README / quickstart / examples alignment
    changelog: CHANGELOG.md
    full_test_suite_evidence: uv run pytest -q
    diff_commit_hygiene: git diff --check
    sdk_owned_release_evidence: true
    deployment_owned: does not run CI/CD, signing, publishing, deployment approval
  production_reference_example:
    phase: "Phase 101: Production Reference Example"
    example: src/agentos/examples/production_reference_web_agent.py
    tests: tests/examples/test_production_reference_web_agent.py
    name: production reference web agent
    service: AgentServiceReference
    runtime_profile: DistributedWebRuntimeProfile
    state_plane: Nacos/Redis/Postgres state plane
    readiness: readiness endpoint
    backend_verification: backend verification
    evidence_bundle: ProductionReadinessEvidenceBundle
    reference_state_plane: ReferenceStatePlaneStack
    probe_pack: ReferenceLiveBackendProbePack
    planner: planner primitive
    backend_client_creation: does not create backend clients
    demo_runtime_guard: demo runtime blocks production readiness by default
    deployment_owned: deployment-owned real infrastructure
  persistence:
    session_snapshot_store: memory | sqlite | filesystem | postgres
    requires_custom_session_provider: true | false
    session_locking: none | required
    hot_store: redis
    hot_store_url: redis://localhost:6379
    durable_memory_store: postgres
    durable_memory_store_dsn: <connection string>
  state_plane:
    profile: ProductionStatePlaneDeploymentProfile
    reference_stack:
      phase: "Phase 97: Reference State Plane Stack"
      stack: ReferenceStatePlaneStack
      profile: ReferenceStatePlaneStackProfile
      required_components: REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS
      purpose: reference state plane
      aggregation: readiness source aggregation
      evidence: component identity evidence
      readiness_bundle: ProductionReadinessEvidenceBundle
      includes: [NacosAgentRegistryAdapter, RedisAgentMessageQueue, PostgresTaskStore, PostgresPlanStore, WorkerProcessSupervisor, LocalSubprocessWorkerSupervisor, SessionSnapshotPersistence, PostgresSessionSnapshotPersistence, AgentServiceReference, DistributedWebRuntimeProfile]
      backend_client_creation: does not create backend clients
      deployment_owned: credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned
    probe_name: production_state_plane
    agent_registry: NacosAgentRegistryAdapter | NacosAgentCardResolver | static | custom
    registry_client_boundary: NacosRegistryClient | custom
    registry_config: NacosRegistryConfig | custom
    registry_evidence: NacosRegistryEvidence
    message_queue: RedisAgentMessageQueue | custom
    task_store: PostgresTaskStore | custom
    plan_store: PostgresPlanStore | custom
    worker_process_supervisor: WorkerProcessSupervisor | deployment-owned
    worker_process_supervisor_adapter: LocalSubprocessWorkerSupervisor | deployment-owned | custom
    worker_process_supervisor_boundary:
      spec: WorkerProcessSpec
      state: WorkerProcessState
      protocol: WorkerProcessSupervisor
      supported_worker_kinds: [team_worker, planner_worker, a2a_push_worker]
      evidence_fields: [status, pid, exit_code, started_at, last_heartbeat_at, stop_requested_at, stopped_at, error, worker_kind, command, env_keys]
      heartbeat_boundary: evidence timestamp only, not health/readiness or restart policy
      command_policy: argv-only
      shell_parsing: no shell parsing
      production_gap: not a Kubernetes, systemd, autoscaling, or secret-distribution layer
    session_snapshot_persistence: SessionSnapshotPersistence | PostgresSessionSnapshotPersistence | custom
    state_plane_boundary_policy:
      - registry is not task truth
      - registry is not plan truth
      - registry is not session snapshot storage
      - registry is not message queue
      - registry is not worker runtime state
      - queue is not final task or plan state
    live_backend_verification:
      profile: DeploymentLiveBackendVerificationProfile
      record: BackendVerificationRecord
      gate: DeploymentLiveBackendVerificationGateReport
      invocation_plan: BackendVerificationInvocationPlan
      runner_protocol: BackendVerificationRunner
      reference_runner: BackendVerificationCliRunner
      report_importer: BackendVerificationReportImporter
      report_import_error: BackendVerificationReportImportError
      run_result: DeploymentLiveBackendVerificationRunResult
      reference_probe_pack:
        phase: "Phase 98: Live Backend Probe Pack"
        pack: ReferenceLiveBackendProbePack
        spec: ReferenceLiveBackendProbeSpec
        pack_name: REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME
      example_module: agentos.examples.live_backend_probe
      default_status: default status is unknown
      certification_claim: non-certifying example
      probes: [Nacos probe, Redis probe, Postgres task/plan/session probe, worker supervisor probe]
        invocation: BackendVerificationInvocationPlan
        run_result: DeploymentLiveBackendVerificationRunResult
        readiness: readiness bundle aggregation
        bundle: ProductionReadinessEvidenceBundle
        backend_client_creation: does not create backend clients
        deployment_owned: credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned
      required_backends: LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
      probe_name: deployment_live_backend_verification
      required_backend_names: [agent_registry, message_queue, task_store, plan_store, worker_process_supervisor, session_snapshot_persistence]
      block_policy: block_production_readiness on missing or failed backend evidence
      execution: deployment-owned backend check execution
      reference_runner_boundary: argv-only, no shell parsing, report path or stdout JSON import, bounded stdout/stderr summaries, env_keys, redacted secret values, no backend client claim, not a live backend client
      deployment_owned: [credentials and secret distribution, CI matrix execution, alert routing and runbooks]
    release_gate:
      bundle: ProductionReadinessEvidenceBundle
      check: ReadinessEvidenceCheck
      status: ReadinessEvidenceStatus
      purpose: release gate evidence bundle
      consumes: existing readiness/profile/backend evidence
      outputs: [accepted, blocking_checks, missing_required_checks, block_production_readiness, sdk_owned, deployment_owned]
      evidence: JSON-safe evidence bundle
      execution: does not execute real infrastructure checks
  service:
    layer: Agent Service Reference Layer | custom app service | deployment-owned platform
    reference_service: AgentServiceReference
    profile: AgentServiceReferenceProfile
    required_components: AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS
    asgi: AsgiAgentApp composition
    runtime_profile_injection: DistributedWebRuntimeProfile injection
    auth_rate_limit: auth/rate-limit hook injection
    readiness: readiness check aggregation
    evidence: JSON-safe readiness evidence
    boundary: reference service, not a platform
    deployment_owned: [gateway/TLS/CORS/WAF, tenant directory, Kubernetes/systemd/autoscaling, credentials, migrations, alerting, live backend verification]

production_readiness:
  form_id: <agentos.readiness form id>
  overall_level: direct | primitives-ready | future-extension
  required_app_glue: [<gap to implement before production>]
  dimensions:
    session_state:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    concurrency:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    auth:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    rate_limit:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    timeout:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    retry:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    observability:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    workspace:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    protocol:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    persistence:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>
    schema_migration:
      level: direct | primitives-ready | future-extension | not-applicable
      evidence: [<SDK evidence, tests, docs, or deployment evidence ref>]
      gap: <remaining SDK or deployment-owned gap, empty when none>

tools:
  - name: <tool-name>
    description: <LLM-facing description>
    parameters:
      type: object
      properties: {}
      required: []
    handler: <module.path:function_name>
    sensitivity: normal | approval-required | sandbox-required
    sandbox:
      policy: none | WorkspaceToolSandboxPolicy
      path_arguments: [<argument-name>]
      required_capabilities: [<capability>]
      workspace_backend:
        protocol: WorkspaceExecutionBackend | SandboxBackend | none
        request: WorkspaceExecutionRequest
        result: WorkspaceExecutionResult
        policy: WorkspaceExecutionPolicy
        local_reference_adapter: LocalWorkspaceExecutionBackend
        command_policy: argv-only
        evidence: JSON-safe execution evidence
        evidence_fields: [exit_code, started_at, finished_at, timed_out, env_keys]
        production_backend: Docker/E2B/enterprise runner | custom | deployment-owned
        production_gap: "LocalWorkspaceExecutionBackend is not a production isolation boundary; Docker/E2B/enterprise runner adapters, OS/container sandboxing, network policy, resource enforcement, sandbox image patching, and live sandbox backend verification remain deployment-owned."

mcp_servers:
  - name: <server-name>
    command: [<cmd>, <args>]
    env: {}

context:
  compression:
    enabled: true | false
    strategy: rule-based | llm-based | fallback
    budget:
      max_active_messages: 20
      retain_latest_messages: 6
  working_state:
    initial_schema:
      - name: <field>
        type: <str | list[str] | dict>
        purpose: <when to update this field>
  recall:
    enabled: true | false
    index: qdrant | custom

multi_agent:
  mode: single | local-spawn | distributed-task | team-primitives | planner-primitives
  workers:
    - name: <worker-name>
      description: <what this worker does>
      tools: [<tool-names>]
      model: <optional model override>
  remote_agents:
    - name: <agent-name>
      endpoint: <url>
      description: <capability description>
  team:
    readiness: primitives-ready
    store: in-memory | postgres
    worker_session_provider: in-memory | custom
    worker_runner: batch | daemon | custom
    worker_retry:
      policy: none | fixed-backoff | exponential-backoff
      store: in-memory | postgres | custom
    worker_cancellation:
      store: in-memory | postgres | custom-persistent
    worker_permission:
      policy: TeamWorkerPermissionPolicy
      workspace_narrowing: enforced
      capability_allow_list: [<capability>]
      sandbox_policy: WorkspaceToolSandboxPolicy
      workspace_backend: WorkspaceExecutionBackend | SandboxBackend | LocalWorkspaceExecutionBackend | custom
      backend_request: WorkspaceExecutionRequest
      backend_result: WorkspaceExecutionResult
      backend_policy: WorkspaceExecutionPolicy
      backend_evidence: JSON-safe execution evidence
      backend_env_evidence: env_keys
      production_backend: Docker/E2B/enterprise runner | deployment-owned
      os_container_sandbox: app-owned
    ui_stream:
      protocol: TeamUiStreamStore
      store: in-memory | postgres | custom-distributed
      replay_cursor: after_event_id
      network_endpoint: team-ui-json-replay
      live_follow: team-ui-sse-follow
    tools: enabled | disabled
    worker_lifecycle:
      profile: WorkerProcessLifecycleDeploymentProfile
      configured_components: [process_supervisor, restart_policy, graceful_shutdown, health_probe, readiness_probe, scaling_policy, credential_policy, migration_policy, alerting, live_backend_verification]
      execution: deployment-owned | LocalSubprocessWorkerSupervisor | custom
      spec: WorkerProcessSpec
      state: WorkerProcessState
      protocol: WorkerProcessSupervisor
      local_reference_adapter: LocalSubprocessWorkerSupervisor
      worker_kind: team_worker
      evidence: JSON-safe lifecycle evidence
      evidence_fields: [exit_code, started_at, last_heartbeat_at, stop_requested_at, stopped_at, env_keys]
      heartbeat_boundary: evidence timestamp only, not health/readiness or restart policy
      command_policy: argv-only
      shell_parsing: no shell parsing
      production_gap: not a Kubernetes, systemd, autoscaling, or secret-distribution layer
    state_plane:
      profile: ProductionStatePlaneDeploymentProfile
      required_components: [agent_registry, message_queue, task_store, plan_store, worker_process_supervisor, session_snapshot_persistence, state_plane_boundary_policy, live_backend_verification]
    sdk_gap: "Team records/messages/wakeup primitives, TeamTools, PostgresTeamStore, TeamWorkerSessionProvider, TeamWorkerPermissionPolicy, WorkspaceToolSandboxPolicy, WorkspaceExecutionBackend, SandboxBackend, LocalWorkspaceExecutionBackend, TeamWorkerRunner, TeamWorkerDaemon, WorkerProcessLifecycleDeploymentProfile, TeamWorkerRetryPolicy, PostgresTeamWorkerRetryStore, TeamWorkerCancellationStore, PostgresTeamWorkerCancellationStore, TeamUiStreamStore, InMemoryTeamUiStreamStore, PostgresTeamUiStreamStore, team UI JSON replay endpoint, and team UI SSE/follow endpoint exist; actual process supervision/scaling execution, Docker/E2B/enterprise runner adapter implementation, and OS/container sandboxing remain app-owned for untrusted code execution."
  planner:
    readiness: primitives-ready
    store: in-memory | postgres | custom-persistent
    retry:
      policy: none | fixed-backoff | exponential-backoff
      max_attempts: <number>
      backoff_seconds: <number>
    templates:
      - template_id: <template-id>
        role: <subagent role>
        capabilities: [<capability>]
        allowed_tool_names: [<tool-name>]
        workspace_scope: task | session | shared
        target_agent_id: <optional persistent expert id>
    tools: enabled | disabled
    decomposition_policy:
      raw_proposal_gate: PlannerRuntime.gate_decomposition_proposal
      raw_proposal_report: PlanDecompositionGateReport
      validation: PlannerRuntime.validate_decomposition
      report: PlanDecompositionValidationReport
      profile: PlannerDecompositionPolicyDeploymentProfile
      configured_components: [prompt_policy, output_schema, validation_gate, template_mapping_policy, approval_policy, model_routing_policy, evaluation_policy, trace_logging, rollback_policy]
      governance_profile: PlannerLlmDecompositionGovernanceProfile
      governance_execution_record: PlannerLlmGovernanceEvidenceRecord
      governance_execution_gate: PlannerRuntime.gate_llm_governance_evidence
      governance_execution_report: PlannerLlmGovernanceEvidenceGateReport
      governance_execution_required_evidence: [prompt_evidence, model_evidence, approval_evidence, evaluation_evidence, validation_evidence]
      per_proposal_governance_evidence_gate: true
      llm_prompt_model_approval_evaluation: deployment-owned
      prompt_model_approval_evaluation_validation_execution: deployment-owned
    worker_lifecycle:
      profile: WorkerProcessLifecycleDeploymentProfile
      configured_components: [process_supervisor, restart_policy, graceful_shutdown, health_probe, readiness_probe, scaling_policy, credential_policy, migration_policy, alerting, live_backend_verification]
      execution: deployment-owned | LocalSubprocessWorkerSupervisor | custom
      spec: WorkerProcessSpec
      state: WorkerProcessState
      protocol: WorkerProcessSupervisor
      local_reference_adapter: LocalSubprocessWorkerSupervisor
      worker_kind: planner_worker
      evidence: JSON-safe lifecycle evidence
      evidence_fields: [exit_code, started_at, last_heartbeat_at, stop_requested_at, stopped_at, env_keys]
      heartbeat_boundary: evidence timestamp only, not health/readiness or restart policy
      command_policy: argv-only
      shell_parsing: no shell parsing
      production_gap: not a Kubernetes, systemd, autoscaling, or secret-distribution layer
    scheduler_daemon:
      primitive: PlannerSchedulerDaemon
      state: PlannerSchedulerDaemonState
      plan_ids: explicitly-supplied
      claimed_primitive: PlannerClaimedSchedulerDaemon
      claimed_state: PlannerClaimedSchedulerDaemonState
      claimed_tick: PlannerRuntime.claimed_scheduler_tick
      governance_profile: PlannerSchedulerGovernanceDeploymentProfile
      governance_components: [plan_discovery_policy, tenant_routing_policy, global_fairness_policy, scheduler_lock_policy, leader_election_policy, stale_lease_recovery_policy, worker_dispatch_supervision, live_backend_verification]
      tenant_routing_global_fairness: deployment-owned
      distributed_scheduler_locks: deployment-owned
      process_supervision: deployment-owned
    state_plane:
      profile: ProductionStatePlaneDeploymentProfile
      required_components: [agent_registry, message_queue, task_store, plan_store, worker_process_supervisor, session_snapshot_persistence, state_plane_boundary_policy, live_backend_verification]
    sdk_gap: "Planner tools/state/template/raw-proposal-gate/decomposition-validation/dependency primitives, PlanDecompositionGatePolicy, PlanDecompositionGateReport, PlanDecompositionValidationReport, PlannerDecompositionPolicyDeploymentProfile, PlanRetryPolicy, PlannerSchedulerDaemon, PlannerClaimedSchedulerDaemon, PlannerSchedulerGovernanceDeploymentProfile, step failure/retry metadata, PostgresPlanStore, and WorkerProcessLifecycleDeploymentProfile exist; automatic LLM decomposition prompt/model/approval/evaluation policy, real tenant routing, global fairness queues, distributed scheduler locks, process supervision, worker dispatch loops, lifecycle execution, live backend verification, and complex compensation policy remain app-owned."

channel:
  type: programmatic | http | http+sse | internal-a2a-task-bridge
  host: "0.0.0.0"
  port: 8000
  auth:
    type: none | bearer | custom
    token_env: AUTH_TOKEN
  a2a:
    protocol_status: internal_task_bridge | discovery_only
    task_subscribe: enabled | disabled
    agent_card:
      name: <name>
      description: <capability>
      skills: [<skill-name>]

hooks:
  - point: before_provider_call | after_provider_call | before_tool_call | after_tool_call
    handler: <module.path:function_name>
    priority: 100
    purpose: <what this hook does>

observability:
  tracing: true | false
  event_bus: true | false
```

## Generation Rules

1. Only include sections relevant to the user's choices.
2. Tool descriptions must be precise LLM-facing prompts because they go directly into provider requests.
3. Handler paths must be resolvable. Prefer `<project>.tools.<name>:handle_<name>`.
4. For tools with `sensitivity: sandbox-required`, add a `WorkspaceToolSandboxPolicy` entry with path arguments and required capabilities. Also choose `WorkspaceExecutionBackend` / `SandboxBackend` explicitly: use `LocalWorkspaceExecutionBackend` only as the argv-only local reference backend with JSON-safe execution evidence and `env_keys`, and state that it does not inherit the host environment by default, or state which Docker/E2B/enterprise runner adapter is deployment-owned. Make OS/container sandboxing explicit when a tool executes untrusted code, and state that the local backend is not a production isolation boundary.
4b. For production-bound specs, Phase 99: SDK Skill / Spec Generator Finalization requires a `production_design_constraints` block. Treat the skill as a spec generator finalization gate and production agent design constraint generator. The block is an SDK-owned constraint template and must explicitly choose agent form, runtime profile, state plane components, persistence backend, registry backend, queue backend, worker supervisor, A2A exposure, planner/team mode, production readiness checklist, and sandbox posture: trusted tools only | deployment-owned isolation | future adapter. State that the SDK does not create deployment-owned infrastructure. Do not proceed to implementation with an implicit default for any of these fields.
4a. For production-bound specs, include the Phase 96: Release Scope Re-baseline section from `docs/release-scope.md`. State that the first production SDK release supports trusted tools, internal service orchestration, terminal agent, single-node web agent, distributed web agent, team/planner/A2A primitive composition, production state plane, readiness evidence, and audit evidence. State that Sandbox / Docker / E2B / microVM / enterprise runner adapter work is a non-blocking future adapter and not a release blocker, and that the SDK does not promise physical isolation for untrusted code execution. Require a sandbox posture of `trusted tools only`, `deployment-owned isolation`, or `future adapter`, and keep the SDK-owned boundary to `WorkspaceExecutionBackend`, `SandboxBackend`, `LocalWorkspaceExecutionBackend`, policy/capability/path pre-check, JSON-safe execution evidence, and audit evidence.
4c. For production-bound specs, include Phase 100: Release Hardening as a release hardening gate. Require release candidate evidence for public API audit, stable API and experimental API classification, migration index, README / quickstart / examples alignment, `CHANGELOG.md`, full test suite evidence, diff/commit hygiene, and SDK-owned release evidence. Link `docs/release-hardening.md`, `docs/api-stability.md`, and `docs/migrations/README.md`. State that AgentOS does not run CI/CD, signing, publishing, deployment approval.
4d. For production-bound web specs that need the standard release reference shape, include Phase 101: Production Reference Example. Link `src/agentos/examples/production_reference_web_agent.py` and `tests/examples/test_production_reference_web_agent.py`. State that the production reference web agent composes `AgentServiceReference`, `DistributedWebRuntimeProfile`, a Nacos/Redis/Postgres state plane, a readiness endpoint, backend verification, `ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`, `ReferenceLiveBackendProbePack`, and a planner primitive. State that it does not create backend clients and that Nacos, Redis, Postgres, credentials, migrations, CI/CD, process supervision, gateway/TLS, tenant directory, live probes, rollout, rollback, alerting, and sandbox isolation are deployment-owned real infrastructure.
5. For standard multi-node deployment, mark `readiness: primitives-ready`, set `runtime_profile: DistributedWebRuntimeProfile`, set `requires_custom_session_provider: false`, choose Redis lease plus Postgres snapshot adapters, and include explicit TTL, stale lease recovery, migration, auth, and workspace decisions. Add a `service` section when the web agent should use the Agent Service Reference Layer: choose `AgentServiceReference`, `AgentServiceReferenceProfile`, and `AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS`, name AsgiAgentApp composition, DistributedWebRuntimeProfile injection, auth/rate-limit hook injection, readiness check aggregation, and JSON-safe readiness evidence, and state that the reference service is not a platform while gateway/TLS/CORS/WAF, tenant directory, Kubernetes/systemd/autoscaling, credentials, migrations, alerting, and live backend verification execution remain deployment-owned. Add a `state_plane` section using `ProductionStatePlaneDeploymentProfile` and explicitly choose `agent_registry`, `message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, `session_snapshot_persistence`, `state_plane_boundary_policy`, and `live_backend_verification`. Add `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, and `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` when the spec needs a reference state plane that composes `NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`, `WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`, `AgentServiceReference`, `DistributedWebRuntimeProfile`, and `ProductionReadinessEvidenceBundle` through readiness source aggregation and component identity evidence; state that it does not create backend clients and that credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned. For `live_backend_verification`, add `DeploymentLiveBackendVerificationProfile`, `BackendVerificationRecord`, `DeploymentLiveBackendVerificationGateReport`, `LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS`, `deployment_live_backend_verification`, and `block_production_readiness`, and state that missing or failed backend evidence blocks production readiness while backend check execution, credentials and secret distribution, CI matrix execution, alert routing and runbooks remain deployment-owned. For Phase 98: Live Backend Probe Pack, add `ReferenceLiveBackendProbePack`, `ReferenceLiveBackendProbeSpec`, `REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME`, `agentos.examples.live_backend_probe`, Nacos probe, Redis probe, Postgres task/plan/session probe, worker supervisor probe, `BackendVerificationInvocationPlan`, `DeploymentLiveBackendVerificationRunResult`, readiness bundle aggregation, and `ProductionReadinessEvidenceBundle`; state that it does not create backend clients and that credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned. Add a release gate section using `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`, and `ReadinessEvidenceStatus`; state that this release gate evidence bundle consumes existing readiness/profile/backend evidence, emits `accepted`, `blocking_checks`, `missing_required_checks`, `block_production_readiness`, `sdk_owned`, `deployment_owned`, and a JSON-safe evidence bundle, and does not execute real infrastructure checks. When a reference runner is useful, add `BackendVerificationInvocationPlan`, `BackendVerificationRunner`, `BackendVerificationCliRunner`, `BackendVerificationReportImporter`, `BackendVerificationReportImportError`, and `DeploymentLiveBackendVerificationRunResult`; state that the reference runner is argv-only, uses no shell parsing, imports a report path or stdout JSON, records bounded stdout/stderr summaries and `env_keys`, redacts configured secret values, emits a no backend client claim, and is not a live backend client. Use `NacosAgentRegistryAdapter`, `NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, and `NacosRegistryEvidence` or a custom registry for AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering, capability-based discovery, Nacos namespace_id evidence, JSON-safe evidence, and discovery-only Nacos metadata; Nacos namespace_id is passed to register, unregister, list, resolve, and discover; Nacos is not task truth, not plan truth, not session snapshot storage, not message queue, and not worker runtime state. Use `RedisAgentMessageQueue` for delivery/wakeup, `PostgresTaskStore` for task truth, `PostgresPlanStore` for plan truth, `WorkerProcessSupervisor` for process lifecycle evidence, and `SessionSnapshotPersistence` for context/messages/compression/working-state snapshots. For local/reference worker process evidence choose `LocalSubprocessWorkerSupervisor`; for production choose `deployment-owned` or a custom `WorkerProcessSupervisor`. State that `WorkerProcessSpec` is argv-only, uses no shell parsing, rejects secret-like metadata keys, emits `WorkerProcessState` JSON-safe lifecycle evidence with `exit_code`, `started_at`, `last_heartbeat_at`, `stop_requested_at`, `stopped_at`, and `env_keys`, that `heartbeat` records an evidence timestamp only and is not health/readiness or restart policy, LocalSubprocessWorkerSupervisor does not inherit the host environment by default, and it is not a Kubernetes, systemd, autoscaling, or secret-distribution layer. WorkerProcessSpec rejects secret-like metadata keys. State that registry is not task truth and queue is not final task or plan state. Set `requires_custom_session_provider: true` only for non-standard lifecycle policy. Nacos credentials and live backend verification execution remain deployment-owned.
6. For compression, default budget is `max_active_messages=20` and `retain_latest_messages=6`.
7. For team discussion, include the `team` subsection, use `PostgresTeamStore` for multi-node team state, use `TeamWorkerSessionProvider` for independent worker session registration, use `TeamWorkerPermissionPolicy` for worker workspace/capability downgrade, use `WorkspaceToolSandboxPolicy` for worker tool path/capability pre-execution checks, choose `WorkspaceExecutionBackend` / `SandboxBackend` for worker workspace execution, use `LocalWorkspaceExecutionBackend` only as the local reference adapter with argv-only JSON-safe execution evidence, use `TeamWorkerRunner` for batch team-message continuation processing, use `WorkerProcessLifecycleDeploymentProfile` when worker services need lifecycle readiness metadata, use `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and `LocalSubprocessWorkerSupervisor` when local `team_worker` processes need reference start/stop/wait/heartbeat evidence, use `ProductionStatePlaneDeploymentProfile` when team worker registry/queue/task truth/worker lifecycle/session snapshot ownership must be audited, use `TeamUiStreamStore` plus `InMemoryTeamUiStreamStore` for local/prototype UI projection, use `PostgresTeamUiStreamStore` plus the team UI JSON replay and SSE/follow endpoints for multi-node UI replay/follow, and state the Docker/E2B/enterprise runner and OS/container sandbox gaps when tools execute untrusted code.
8. For planner / intent-router, include the `planner` subsection with `SubAgentTemplate` intent, plan store choice, planner tool usage, `PlannerRuntime.validate_decomposition` and `PlanDecompositionValidationReport` usage before `plan_create_from_decomposition` when a structured plan proposal is available, `PlannerDecompositionPolicyDeploymentProfile` usage for `prompt_policy`, `output_schema`, `validation_gate`, `template_mapping_policy`, `approval_policy`, `model_routing_policy`, `evaluation_policy`, `trace_logging`, and `rollback_policy`, `PlannerLlmDecompositionGovernanceProfile` usage for governance reference readiness payloads, `PlannerLlmGovernanceEvidenceRecord`, `PlannerRuntime.gate_llm_governance_evidence`, and `PlannerLlmGovernanceEvidenceGateReport` usage as the per-proposal governance evidence gate before production plan creation, explicit planner LLM governance execution evidence for `prompt_evidence`, `model_evidence`, `approval_evidence`, `evaluation_evidence`, and `validation_evidence`, `plan_ready_steps` usage when dependency-aware scheduling is needed, `PlanRetryPolicy` plus `plan_fail_step`/`plan_retryable_steps`/`plan_retry_step` usage when failed steps should be recovered with attempt/backoff metadata, `PlannerSchedulerDaemon` usage when a supervised service should poll explicitly supplied plan ids through `PlannerRuntime.scheduler_tick(...)`, `PlannerClaimedSchedulerDaemon` usage when a supervised service should run claimed scheduler daemon polling through `PlannerRuntime.claimed_scheduler_tick(...)`, `PlannerSchedulerGovernanceDeploymentProfile` usage when production scheduler governance needs readiness metadata for `plan_discovery_policy`, `tenant_routing_policy`, `global_fairness_policy`, `scheduler_lock_policy`, `leader_election_policy`, `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and `live_backend_verification`, `WorkerProcessLifecycleDeploymentProfile` usage when planner workers or scheduler services need lifecycle readiness metadata, `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and `LocalSubprocessWorkerSupervisor` usage when local `planner_worker` processes need reference start/stop/wait/heartbeat evidence, `ProductionStatePlaneDeploymentProfile` usage when planner registry, message queue, task truth, plan truth, worker lifecycle evidence, and session snapshot ownership must be audited, `plan_to_working_state_summary` projection usage, and remaining LLM prompt/model/approval/evaluation/real-tenant-routing/fairness-queue/distributed-lock/lifecycle-execution/compensation gaps. State that deployment-owned prompt/model/approval/evaluation/validation execution still owns the actual prompt run, model router, approval workflow, evaluation suite, schema validation, artifact retention, and certification decision. Use `PostgresPlanStore` plus the plan-store migration when plans must survive restarts or cross nodes.
9. Do not describe current A2A support as full A2A compliance. Use `internal-a2a-task-bridge` for `/a2a/tasks`; add A2A Agent Card publication/discovery only when the spec needs external discovery, and enable task subscribe only when the app exposes `POST /a2a/tasks/{id}:subscribe` SSE updates. For production A2A exposure, state that `A2AOperationServer` defaults to `RejectAllA2AInboundAuthPolicy`, default inbound A2A operation auth is fail-closed, A2AOperationServer default rejects unauthenticated peers, `AllowAllA2AInboundAuthPolicy` is explicit local/dev opt-in, local/dev peers must opt in explicitly, and `A2AOperationClient` uses the A2AOperationClient default public HTTPS egress policy unless a stricter allow-list policy is supplied. Add an external conformance section that chooses `A2AExternalConformanceInvocationPlan`, `A2AExternalConformanceInvocationGateReport`, and either a deployment-owned suite runner or the SDK reference `A2AExternalConformanceCliRunner`. When using the CLI runner, state that it is argv-only, uses no shell parsing, imports a report path or stdout JSON, records bounded stdout/stderr summaries, exposes only `env_keys`, redacts secret values, and returns `A2AExternalConformanceExecutionRecord` for `A2AExternalConformanceGateReport` local release gating with no certification claim. Official suite selection/installation, CI matrix execution, artifact storage, network trust rollout, live backend verification, and certification attestation remain deployment-owned.
10. For production-bound agents, call `get_agent_form_readiness(form_id)` from `agentos.readiness`, populate `production_readiness`, and include every `required_app_glue` gap before claiming production readiness.

## After Generation

Present the spec to the user and ask them to confirm or adjust it. Once confirmed, proceed to `flow/03-implementation.md`.
