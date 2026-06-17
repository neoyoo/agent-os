# Production Readiness

`agentos.readiness` is the structured source of truth for SDK-level agent-form
readiness. Use `get_agent_form_readiness(form_id)` when an agent spec is meant
for production, then copy the result into the spec's `production_readiness`
section.

Readiness is not a live health check. It is a versioned capability matrix that
separates SDK-owned primitives from application glue that must be implemented
before production use.

For the long-running architecture-review objective, use
`docs/agentos-objective-coverage-audit.md` as the coverage ledger that maps the
original terminal, web, A2A, team, planner, workspace, and SDK-skill goals to
current evidence and remaining blockers.

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

Phase 99: SDK Skill / Spec Generator Finalization makes the agent-os skill a
spec generator finalization gate and a production agent design constraint
generator, not only a runtime tutorial. Every production-bound spec must carry
an SDK-owned constraint template named `production_design_constraints`.

That `production_design_constraints` block must explicitly choose the agent
form, runtime profile, state plane components, persistence backend, registry
backend, queue backend, worker supervisor, A2A exposure, planner/team mode,
production readiness checklist, and sandbox posture: trusted tools only |
deployment-owned isolation | future adapter. The generated spec must also
state that the SDK does not create deployment-owned infrastructure.

This keeps AgentOS boundary-first. The SDK-owned constraint template records
architecture decisions, readiness evidence expectations, and deployment-owned
gaps. Deployment code still owns concrete infrastructure creation, credentials,
migrations, Kubernetes/systemd/autoscaling, tenant directory integration,
backend probe execution, and physical isolation policy.

## Release Hardening

Phase 100: Release Hardening makes the long-running review branch a
release-candidate SDK branch instead of continuing to add runtime features. The
release hardening gate requires release candidate evidence for public API
audit, stable API and experimental API classification, migration index,
README / quickstart / examples alignment, `CHANGELOG.md`, full test suite
evidence, diff/commit hygiene, and runtime boundary scans for `QueryLoop` and
`AsyncQueryLoop`.

The canonical release-hardening files are `docs/release-hardening.md`,
`docs/api-stability.md`, `docs/migrations/README.md`, and `CHANGELOG.md`.
These files record SDK-owned release evidence and API stability classification.
AgentOS does not run CI/CD, signing, publishing, deployment approval. CI/CD,
artifact signing, package publishing, release approval, backend credentials,
migration execution, rollout, rollback, and physical isolation remain
deployment-owned.

## Production Reference Example

Phase 101: Production Reference Example adds the production reference web
agent at `src/agentos/examples/production_reference_web_agent.py`, with tests
in `tests/examples/test_production_reference_web_agent.py`. This example is
the copyable first-release assembly path for a production web agent.

The composition includes `AgentServiceReference`,
`DistributedWebRuntimeProfile`, a Nacos/Redis/Postgres state plane, a
readiness endpoint, backend verification,
`ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, and a planner primitive. The planner primitive
uses the existing plan-and-execute example and records `PlannerRuntime`
evidence.

The production reference web agent is SDK-owned release evidence, not platform
provisioning. It does not create backend clients. Nacos, Redis, Postgres,
credentials, migrations, live backend probe execution, Kubernetes/systemd,
autoscaling, CI/CD, tenant directory integration, gateway/TLS/CORS/WAF,
runbooks, alerting, rollout, rollback, and sandbox isolation are
deployment-owned real infrastructure.

The demo runtime blocks production readiness by default: imported backend
verification evidence proves deployment-owned probes ran, but the reference app
still uses demo Memory/InMemory runtime bindings unless a deployment injects
real Redis/Postgres/Nacos state-plane clients. `allow_demo_runtime_readiness` is
an explicit demo-fixture opt-in, not a production signal.

## Skill Release Governance

AgentOS is both a runtime SDK and a developer guidance skill. Use
`SkillReleaseManifest`, `SkillReleaseFile`,
`build_skill_release_manifest(...)`, `SkillReleaseDriftReport`, and
`compare_skill_release_manifests(...)` when release automation needs to compare
the repository skill at `.claude/skills/agent-os` with an installed user-level skill.
The SDK-owned boundary is deterministic file enumeration, SHA-256 file hashing,
a release manifest, and a drift report covering missing, extra, changed, and
version-mismatched files.

This protects version synchronization without turning the SDK into a skill
installer or marketplace publisher. The install/copy/publish/sign approval remains deployment-owned,
including overwrite prompts, plugin cache busting, marketplace entries, release
approval, artifact signing, and CI policy for whether drift blocks a release.

## Production State Plane Boundary

Use `ProductionStatePlaneDeploymentProfile` when a production deployment needs
one JSON-safe readiness contract for the `production_state_plane`. This profile
keeps discovery, delivery, truth state, worker lifecycle evidence, and session
runtime snapshots separate before concrete backends are wired.

Required state-plane components:

- `agent_registry`: AgentCard, endpoint, capabilities, version, and health
  metadata discovery. The recommended adapter boundary is
  `NacosAgentRegistryAdapter` or a custom registry adapter. The registry is not task truth.
- `message_queue`: worker messages, inbox, wakeup, delivery, and fan-out hints.
  The recommended adapter boundary is `RedisAgentMessageQueue`. The queue is not final task or plan state.
- `task_store`: task truth, task result, retry state, and assignment evidence.
  The recommended adapter boundary is `PostgresTaskStore`.
- `plan_store`: plan truth, claims, scheduler recovery metadata, and planner
  evidence. The recommended adapter boundary is `PostgresPlanStore`.
- `worker_process_supervisor`: local worker start/running/stop/exit/fail
  evidence, including exit code and timestamp metadata. The recommended
  boundary is `WorkerProcessSupervisor`.
- `session_snapshot_persistence`: context, messages, compression, working
  state, and session runtime snapshot persistence. The recommended boundary is
  `SessionSnapshotPersistence`.
- `state_plane_boundary_policy`: deployment documentation that prevents
  registry, queue, task/plan truth stores, worker process lifecycle, and
  session snapshot responsibilities from being mixed.
- `live_backend_verification`: deployment-owned checks that the chosen Nacos,
  Redis, Postgres, supervisor, and snapshot backends are reachable and correctly
  configured.

AgentOS owns the profile, component names, missing-component detection,
readiness payload shape, and guidance. Deployments own Nacos/Redis/Postgres
credentials, migrations, worker supervisor choice, secret distribution, tenant
directory integration, autoscaling, alerting, runbooks, and live backend
verification.

### Live Backend Verification Evidence Boundary

Use `BackendVerificationRecord`,
`DeploymentLiveBackendVerificationGateReport`,
`DeploymentLiveBackendVerificationProfile`, and
`LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS` when a production deployment
needs to prove the configured `agent_registry`, `message_queue`, `task_store`,
`plan_store`, `worker_process_supervisor`, and
`session_snapshot_persistence` backends were actually checked. The readiness
probe is `deployment_live_backend_verification`; it sets
`block_production_readiness=True` when there is missing or failed backend evidence.

This is an evidence consumption boundary, not a live checker. AgentOS owns the
JSON-safe record shape, missing-backend detection, failed/skipped/unknown status
classification, and readiness payload. Deployment owns backend check execution,
credentials and secret distribution, migration execution, network/TLS policy,
CI matrix execution, alert routing and runbooks, release approval, and
certification.

### Live Backend Verification Reference Runner Boundary

Use `BackendVerificationInvocationPlan`, `BackendVerificationRunner`,
`BackendVerificationCliRunner`, `BackendVerificationReportImporter`,
`BackendVerificationReportImportError`, and
`DeploymentLiveBackendVerificationRunResult` when a deployment wants a
reference runner that invokes its own backend check script and converts a
report path or stdout JSON into `BackendVerificationRecord` values.

The reference runner is argv-only, uses no shell parsing, captures bounded
stdout/stderr summaries, records only `env_keys`, redacts configured secret
values, and emits `no backend client claim` metadata. A timeout, nonzero exit,
missing report, malformed report, or missing required backend evidence blocks
production readiness through the same live backend verification gate.

This is a reference runner, not a live backend client. AgentOS owns invocation
evidence, report normalization, and JSON-safe readiness payloads. Deployments
own Nacos, Redis, Postgres, supervisor, session snapshot, Docker, E2B,
Kubernetes, or systemd clients; credentials and secret distribution; migrations;
CI matrix execution; alert routing and runbooks; release approval; and
certification.

### Live Backend Probe Pack

Phase 98: Live Backend Probe Pack adds `ReferenceLiveBackendProbePack`,
`ReferenceLiveBackendProbeSpec`, and
`REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME` as the SDK-owned reference probe pack
for state-plane backend evidence. The pack declares a Nacos probe for
`agent_registry`, a Redis probe for `message_queue`, Postgres task/plan/session
probe entries for `task_store`, `plan_store`, and
`session_snapshot_persistence`, and a worker supervisor probe for
`worker_process_supervisor`.

The probe pack builds argv-only `BackendVerificationInvocationPlan` values for
each backend and aggregates `DeploymentLiveBackendVerificationRunResult`
objects into `ProductionReadinessEvidenceBundle` through readiness bundle
aggregation. The default invocation points at
`agentos.examples.live_backend_probe`, a reference stdout JSON report emitter
that shows the expected report shape while making a no-backend-client claim.
It does not create backend clients. Its default status is unknown and it is a
non-certifying example; deployments must run their own live backend checks and
explicitly provide passed records with external evidence references.

AgentOS owns probe declarations, invocation metadata, JSON-safe example
reports, component identity evidence, readiness bundle aggregation, and the
reference example entrypoint. credentials, migrations, CI matrix execution,
alert routing and runbooks remain deployment-owned.

### Production Readiness Evidence Bundle Boundary

Use `ProductionReadinessEvidenceBundle`, `ReadinessEvidenceCheck`, and
`ReadinessEvidenceStatus` when a release needs one JSON-safe evidence bundle
over already-produced readiness/profile/backend evidence. This release gate evidence bundle consumes existing readiness/profile/backend evidence and reports
`accepted`, `blocking_checks`, `missing_required_checks`, and
`block_production_readiness`.

The SDK-owned side is normalization, secret-like key redaction, JSON-safe
evidence bundle serialization, and `sdk_owned` / `deployment_owned` boundary
metadata. The deployment-owned side is backend check execution, credentials,
migrations, CI matrix execution, network/TLS/gateway policy, artifact
retention, release approval, rollout, rollback, alerting, and runbooks. The
bundle does not execute real infrastructure checks.

### Reference State Plane Stack

Phase 97: Reference State Plane Stack adds `ReferenceStatePlaneStack`,
`ReferenceStatePlaneStackProfile`, and
`REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` as the SDK-owned reference state
plane composition. This layer proves that `NacosAgentRegistryAdapter`,
`RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`,
`WorkerProcessSupervisor`, `LocalSubprocessWorkerSupervisor`,
`SessionSnapshotPersistence`, `PostgresSessionSnapshotPersistence`,
`AgentServiceReference`, `DistributedWebRuntimeProfile`, and
`ProductionReadinessEvidenceBundle` can be combined through readiness source
aggregation and component identity evidence.

The reference state plane does not create backend clients. It accepts already
constructed adapters, profiles, and service references, records JSON-safe audit
evidence, and builds a `ProductionReadinessEvidenceBundle` over existing
readiness sources. credentials, migrations, CI matrix execution, alert routing
and runbooks remain deployment-owned.

### Nacos Registry Adapter Boundary

Use `NacosAgentRegistryAdapter`, `NacosAgentCardResolver`,
`NacosRegistryClient`, `NacosRegistryConfig`, and `NacosRegistryEvidence` when
a deployment wants Nacos-backed AgentCard discovery. The SDK-owned boundary is
AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering,
capability-based discovery, Nacos namespace_id evidence, and JSON-safe evidence.
Nacos namespace_id is passed to register, unregister, list, resolve, and discover
so tenant or environment discovery scopes remain explicit.

This is discovery-only Nacos metadata: it is not task truth, not plan truth,
not session snapshot storage, not message queue, and not worker runtime state.
Deployments own the concrete Nacos client package, Nacos credentials, TLS/auth,
namespace and tenant policy, service naming conventions beyond SDK defaults,
and live backend verification.

## Worker Lifecycle Reference Supervisor

Use `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and
`LocalSubprocessWorkerSupervisor` when a local team worker, planner worker, or
A2A push worker needs stable lifecycle evidence without adopting a production
process manager inside the SDK.

The reference adapter is deliberately narrow:

- `WorkerProcessSpec` is an argv-only launch contract. It supports
  `team_worker`, `planner_worker`, and `a2a_push_worker` worker kinds, plus
  optional cwd, env, and metadata. It uses no shell parsing.
  WorkerProcessSpec rejects secret-like metadata keys so lifecycle evidence does
  not carry raw tokens, passwords, or credentials.
- `WorkerProcessState` records JSON-safe lifecycle evidence: `status`, `pid`,
  `exit_code`, `started_at`, `stop_requested_at`, `stopped_at`, `error`,
  `worker_kind`, `command`, `env_keys`, and metadata. Environment values are
  never emitted; only `env_keys` appear in evidence.
- `WorkerProcessSupervisor` is the protocol boundary for `start`, `stop`,
  `wait`, `state`, `is_running`, and `evidence`.
- `LocalSubprocessWorkerSupervisor` is the local subprocess reference adapter
  for tests, development, and simple supervised service hosts. It does not
  inherit the host environment by default; only explicitly supplied env keys are
  passed to the child process and surfaced as `env_keys` evidence.

This gives SDK users a common worker start/running/stop/exit/fail evidence
shape before they wire a company supervisor. It is not a Kubernetes, systemd, autoscaling, or secret-distribution layer. Restart policy, graceful drain,
health/readiness endpoint wiring, worker scaling, credential issuance,
secret distribution, migrations, live backend verification, alert routing,
runbooks, OS/container sandboxing, and log/artifact collection remain
deployment-owned.

## Readiness Levels

| Level | Meaning |
|-------|---------|
| `direct` | The SDK has a stable builder/runtime path and tests for this dimension. |
| `primitives-ready` | The SDK has core primitives, but production policy or adapters remain application-owned. |
| `future-extension` | The SDK does not yet expose a first-class production boundary. |
| `not-applicable` | The dimension is not required for that form by default. |

## Required Dimensions

Every production readiness record covers the same dimensions:

- `session_state`
- `concurrency`
- `auth`
- `rate_limit`
- `timeout`
- `retry`
- `observability`
- `workspace`
- `protocol`
- `persistence`
- `schema_migration`

Treat every non-`direct` and non-`not-applicable` dimension as a delivery item.
A form with `overall_level: primitives-ready` can be a good production base, but
the app must close its `required_app_glue` before being described as production
ready.

## Spec Contract

Production-bound specs should include:

```yaml
production_readiness:
  form_id: <agentos.readiness form id>
  overall_level: direct | primitives-ready | future-extension
  required_app_glue: []
  dimensions:
    session_state: direct | primitives-ready | future-extension | not-applicable
    concurrency: direct | primitives-ready | future-extension | not-applicable
    auth: direct | primitives-ready | future-extension | not-applicable
    rate_limit: direct | primitives-ready | future-extension | not-applicable
    timeout: direct | primitives-ready | future-extension | not-applicable
    retry: direct | primitives-ready | future-extension | not-applicable
    observability: direct | primitives-ready | future-extension | not-applicable
    workspace: direct | primitives-ready | future-extension | not-applicable
    protocol: direct | primitives-ready | future-extension | not-applicable
    persistence: direct | primitives-ready | future-extension | not-applicable
    schema_migration: direct | primitives-ready | future-extension | not-applicable
```

## Workspace Execution Isolation Profile

Use `WorkspaceExecutionIsolationProfile` when a terminal, web, team, or planner
deployment needs a JSON-safe readiness contract for workspace execution
isolation. Configure `workspace_policy`, `tool_path_sandbox`,
`capability_allowlist`, `execution_backend`, `process_isolation`,
`resource_limits`, `network_policy`, and `audit_logging` when those pieces are
present.

The SDK-owned side remains `WorkspaceHandle`, `WorkspaceProvider`,
`WorkspacePolicy`, scope narrowing, `WorkspaceToolSandboxPolicy`,
`ToolPathSandboxRule`, path escape pre-check, and tool capability pre-check.
The deployment-owned side remains OS/container sandboxing, process isolation, filesystem mount policy, network egress policy, CPU and memory limits, secret redaction, audit logging backend, sandbox image/runtime patching, and live sandbox backend verification.

## Workspace / Sandbox Backend Boundary

Use `WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`,
`WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, and
`LocalWorkspaceExecutionBackend` when a tool, team worker, planner worker, or
generated subagent needs a pluggable workspace execution backend. The SDK
contract is argv-only, requires a `WorkspaceHandle`, checks cwd containment
against the workspace root, applies capability allow-list policy, and emits
JSON-safe execution evidence with exit code, timestamps, stdout/stderr byte
counts, metadata, and `env_keys`. Environment values are never emitted, and the
local reference backend does not inherit the host environment by default.

`LocalWorkspaceExecutionBackend` is a local reference adapter for tests,
trusted developer workflows, and simple local tools. It is not a production isolation boundary. Docker/E2B/enterprise runner adapters, container or microVM
isolation, filesystem mount policy, network egress policy, CPU and memory
limits, secret injection, sandbox image patching, production audit storage, and
live sandbox backend verification remain deployment-owned.

## Current Forms

### `terminal-script`: Terminal / Script Agent

Recommended profile: `LocalRuntimeProfile`

This is the strongest direct path today: a single-process agent built with
`AgentBuilder` and the sync `QueryLoop`. It is appropriate for CLI tools,
scripts, local automations, and developer workflows.

Production notes:

- Auth, rate limiting, protocol exposure, and shared schema migration are
  `not-applicable` by default because no network channel is exposed.
- Workspace support is `primitives-ready`; use `WorkspaceToolSandboxPolicy`
  and `ToolPathSandboxRule` for SDK-level path/capability pre-checks. Use
  `WorkspaceExecutionIsolationProfile` when local tools need deployment
  readiness metadata for execution isolation components.
- Use `LocalWorkspaceExecutionBackend` only as the local reference
  `WorkspaceExecutionBackend`; choose a Docker/E2B/enterprise runner adapter
  for production isolation.
- OS/container sandboxing remains deployment-owned for code-interpreter style
  tools.
- Do not force async unless the host already runs an event loop or uses native
  async tools/providers.

### `async-web-host`: Async Web Host Agent

Recommended profile: `WebRuntimeProfile`

This form covers a single-host async web agent using `AsyncQueryLoop`,
`AsgiAgentApp`, JSON/SSE endpoints, channel auth primitives, and rate limiting.

Production notes:

- Auth policy is configurable through SDK primitives, but bearer/custom policy
  enforcement is application-configured.
- Snapshot persistence is available as a primitive; the app must choose restore
  and save lifecycle policy.
- Use `WorkspaceToolSandboxPolicy` for workspace-root path checks and tool
  capability allow-lists before external tool handlers run.
- Use `WorkspaceExecutionIsolationProfile` when the web host must state which
  execution backend, process isolation, resource limit, network policy, and
  audit logging controls are configured.
- Use `WorkspaceExecutionBackend` or `SandboxBackend` as the explicit backend
  slot when web tools need workspace-bound execution. `LocalWorkspaceExecutionBackend`
  is useful for trusted local testing, but it is not a production isolation boundary.
- OS/container sandboxing remains deployment-owned for high-risk execution
  tools.

### `web-distributed-session`: Web Distributed Session Agent

Recommended profile: `DistributedWebRuntimeProfile`

This form covers the target shape where any node may receive a turn for the same
session. The SDK has `DurableAgentSessionProvider`, `SessionLeaseStore`,
`SnapshotAgentFactory`, `SessionSnapshot`, `RedisSessionLeaseStore`, and
`PostgresSessionSnapshotPersistence` boundaries. Use
`DistributedWebRuntimeProfile` as the SDK preset that assembles these pieces for
web channels.

Required app glue:

- Redis lease configuration and TTL policy
- Postgres snapshot migration and credentials
- workspace policy
- failure recovery policy

Production notes:

- This remains `primitives-ready` because deployments must still run migrations,
  configure Redis/Postgres credentials, tune TTLs, and define stale-turn
  recovery.
- The app must define lock timeout, stale lease recovery, snapshot migration,
  and failure behavior.
- Use `DistributedWebSessionOperationsProfile` when a deployment needs a
  JSON-safe readiness contract for distributed web session operations. Configure
  `durable_session_provider`, `lease_store`, `snapshot_persistence`,
  `snapshot_migration`, `lease_ttl_policy`, `stale_lease_recovery`,
  `credential_policy`, `auth_tenant_policy`, `workspace_policy`, and
  `live_backend_verification` when those pieces are present.
- The SDK-owned side remains `DistributedWebRuntimeProfile`,
  `DurableAgentSessionProvider`, `SnapshotAgentFactory`, `SessionLeaseStore`,
  `RedisSessionLeaseStore`, `SessionSnapshot`, `SessionPersistence`,
  `PostgresSessionSnapshotPersistence`, acquire/hydrate/save/release lifecycle,
  lease-fenced snapshot writes, `lease_fence` monotonic fencing evidence, and
  async session provider offload. Lease-fenced snapshot writes are revision CAS
  writes with pre/post lease ownership verification and rollback before commit
  when the post-save lease check fails; they are not a Redis/Postgres distributed
  transaction. The deployment-owned side remains Redis/Postgres credentials, migration execution, lease TTL tuning, stale lease recovery policy, auth and tenant integration, workspace policy configuration, live backend verification, rollout and rollback policy, and alerting and incident response.
- Distributed stream resume readiness is evidence-based. A configured SSE
  buffer, turn-control store, and heartbeat interval only prove
  `distributed_stream_resume_configured`; production readiness additionally
  requires shared backend evidence and cross-node resume evidence before
  `distributed_stream_resume_ready` is reported.
- Use `WorkspaceExecutionIsolationProfile` alongside
  `DistributedWebRuntimeProfile` when multi-node turns need an explicit
  workspace isolation readiness surface.
- Use `ProductionStatePlaneDeploymentProfile` when the same deployment also
  needs to show that `agent_registry`, `message_queue`, `task_store`,
  `plan_store`, `worker_process_supervisor`, `session_snapshot_persistence`,
  `state_plane_boundary_policy`, and `live_backend_verification` are assigned
  to separate production backends.
- Redis hot state, durable memory, and session snapshots are separate concerns;
  do not treat one as a drop-in replacement for another without an adapter that
  implements the required protocol.

### `a2a-discovery`: A2A Discovery Agent

Recommended profile: `DistributedAgentProfile`

This form covers A2A Agent Card publication/discovery plus protocol operation
boundaries for `message/send`, `message/stream`, task lookup, task cancel,
task status subscribe over SSE, and push notification configuration/delivery management. It also
includes SDK-level protocol version negotiation, card signing/trust
verification, and outbound peer-auth header injection primitives. Agent Cards
now model skill-level input/output media modes, `supportedInterfaces`,
preferred transport, and capability extensions for registry/discovery matching.

Required app glue:

- CA trust rollout policy
- deployment process supervision for webhook delivery workers
- worker alerting and restart supervision policy
- DNS pinning and egress proxy policy
- credential issuance and secret distribution policy
- tenant directory and role assignment lifecycle policy

Production notes:

- Current support includes discovery, an internal task bridge, `message/send`,
  `message/stream`, task lookup, task cancel, `tasks/resubscribe`, and
  `POST /a2a/tasks/{id}:subscribe` SSE task status updates. The ASGI
  `POST /a2a/message:stream` route uses the A2A message stream operation
  boundary before SSE starts, and the ASGI subscribe route uses the A2A task
  subscribe operation boundary through
  `A2AOperationServer.handle_task_resubscribe`, so protocol version checks,
  extension negotiation, inbound peer/resource authorization, and local
  per-peer rate limits run before runner/store work, task lifecycle reads, or
  SSE response start.
  It also includes `POST/GET/DELETE
  /a2a/tasks/{task_id}/pushNotificationConfigs...` routes backed by
  `A2APushNotificationConfigStore`, and `A2AProtocolVersionPolicy` for
  outbound `A2A-Version` headers plus inbound unsupported-version rejection
  with the A2A `-32009` error. Text message parts use the A2A 1.0 wrapper
  shape while legacy `kind: text` input is still accepted. File bytes, file URL,
  and structured data message parts also use the A2A 1.0 wrapper shape, with
  legacy `kind: file` and `kind: data` input accepted for transition
  compatibility. Task artifacts now serialize as A2A artifact objects with
  `artifactId` and part wrappers, and task subscribe / push status updates use
  the A2A 1.0 `statusUpdate` wrapper. Artifact update events have an SDK
  `artifactUpdate` serializer/parser boundary. `A2AExtensionNegotiationPolicy`
  parses and emits `A2A-Extensions`, rejects missing required extensions with
  the A2A `-32008` error, and lets optional unsupported extensions degrade by
  default. `A2AConformanceHarness` provides an SDK self-conformance report for
  these local protocol surfaces, including the `message/stream request`
  JSON-RPC envelope, typed `message stream event` SSE payload shape,
  `tasks/resubscribe request` JSON-RPC envelope, and task resubscribe statusUpdate event payload shape, and `A2AExternalConformanceReportImporter`
  can import external conformance result JSON from CI or deployment-owned suite
  runs into the same report model. `A2AExternalConformanceExecutionRecord`
  captures the already-executed suite command, target, exit code, timestamps,
  summaries, artifact URI, and imported report as an external conformance execution record.
  `A2AExternalConformanceGateReport` turns that record into an external conformance gate report for local release/readiness policy. It is not full A2A
  operation compliance or certification.
- Use `A2AAgentSkill` input/output modes, `A2AAgentInterface`, and
  `A2AAgentExtension` to publish discovery metadata that registry clients can
  match before invoking an agent.
- Use `PublicHttpsA2AEgressUrlPolicy` or
  `HostAllowListA2AEgressUrlPolicy` as `egress_url_policy` on
  `A2ACardResolver`, `OidcDiscoveryMetadataProvider`,
  `JwksA2ACardTrustStore`, `JwksA2AJwtVerifier`, `A2AOperationClient`, or
  `A2AAdapter` when outbound Agent Card, OIDC metadata, JWKS, peer operation,
  or internal task bridge calls must be restricted to public HTTPS hosts or
  explicit host/domain allow-lists. DNS pinning, enterprise egress proxying, and
  CA trust rollout remain deployment-owned.
- `A2AOperationClient` uses an A2AOperationClient default public HTTPS egress policy
  when no explicit policy is supplied. `A2AOperationServer` uses
  `RejectAllA2AInboundAuthPolicy` by default: default inbound A2A operation auth
  is fail-closed, and A2AOperationServer default rejects unauthenticated peers
  before runner/store work. `AllowAllA2AInboundAuthPolicy` is explicit local/dev
  opt-in; local/dev peers must opt in explicitly rather than relying on an
  implicit allow-all production default.
  AllowAllA2AInboundAuthPolicy is explicit local/dev opt-in.
- Use `A2AOperationClient.stream_message(...)` to initiate outbound
  `message/stream` through the same Agent Card URL, protocol version header,
  extension negotiation, auth provider, and egress URL policy as
  `send_message(...)`. This is the client-side operation initiation boundary;
  use `A2AOperationClient.stream_message_events(...)` when a client should
  consume the peer `text/event-stream` response through the typed client-side
  SSE event consumption boundary. `A2AMessageStreamEvent` and
  `parse_a2a_sse_events(...)` cover the narrow parser/model projection;
  reconnects, backpressure, durable stream cursors, and fan-out remain
  transport or deployment-owned.
- Use `A2AOperationClient.task_resubscribe(...)` for one-shot cursor
  resubscribe calls to peer `tasks/resubscribe` through the same Agent Card
  URL, protocol version header, extension negotiation, auth provider, trace
  propagation, and egress URL policy as `message/send` and `message/stream`.
  Automatic reconnect loops, durable cursor storage, fan-out, backpressure,
  gateway quota, billing, and credential issuance remain deployment-owned.
- Use `A2AStreamLifecycleDeploymentProfile` when a deployment needs a JSON-safe
  readiness contract for A2A streaming lifecycle policy. The SDK-owned side is
  limited to the `message/stream` operation boundary, typed message stream event
  parser, `tasks/resubscribe` operation boundary, one-shot task resubscribe
  client, and push notification delivery primitives. The deployment-owned
  stream lifecycle list is: automatic reconnect loops, durable cursor storage, fan-out, backpressure, gateway quota, billing, credential issuance, process supervision, DNS pinning, enterprise egress proxy, CA rollout, tenant directory lifecycle, and external conformance execution.
- `InMemoryA2APushNotificationConfigStore` is suitable for tests and local
  prototypes. `A2APushNotificationDispatcher` can POST one task status update
  to one configured webhook with configured auth headers, and
  `HostAllowListA2APushNotificationUrlPolicy` lets deployments restrict
  webhook URLs to exact hosts or domain suffixes while preserving the default
  HTTPS plus public-host guard through
  `PublicHttpsA2APushNotificationUrlPolicy`.
  `A2APushNotificationDeliveryWorker` plus
  `InMemoryA2APushNotificationDeliveryStore` provide enqueue/claim/retry/
  dead-letter primitives for local workers. `A2APushNotificationDaemon` hosts
  that worker as a start/stop/join polling loop and records polling errors
  without coupling webhook delivery to the runtime loop.
  `A2APushNotificationHealthPolicy` and
  `A2APushNotificationHealthReport` classify daemon state as healthy,
  degraded, unhealthy, stopped, or unstarted from polling recency and recent
  errors, giving process supervisors a stable SDK health projection.
  `A2APushNotificationDeploymentProfile` turns that projection into JSON-safe
  `health_check()` and ASGI readiness-compatible `readiness_check()` payloads
  with conservative healthy-only readiness by default. Use
  `PostgresA2APushNotificationConfigStore`,
  `PostgresA2APushNotificationDeliveryStore`, and
  `docs/migrations/2026-06-15-postgres-a2a-push-notifications.sql` when
  webhook config and delivery state must survive restarts or cross nodes.
  Production deployments still need process supervision, alerting/restart
  policy, credentials, DNS pinning or egress proxy controls for SSRF defense
  beyond SDK URL policies, CA rollout, tenant directory and role assignment
  lifecycle, and credential issuance plus secret distribution.
- Use `A2ACardSignature`, `HmacA2ACardSigner`,
  `HmacA2ACardVerifier`, `StaticA2ACardTrustStore`,
  `JwksA2ACardTrustStore`, `RotatingA2ACardTrustStore`, and
  `RotatingHmacA2ACardSigner` when a deployment needs a signed-card trust
  boundary. `JwksA2ACardTrustStore` loads trusted HTTPS JWKS `oct`/HS256 keys
  from explicitly configured URLs with optional key-id allow-lists and TTL
  caching. CA trust rollout, IdP administration, tenant directory, and role
  assignment lifecycle remain deployment-owned.
- Use `StaticBearerA2AAuthProvider` or a custom `A2AAuthProvider` to inject
  outbound peer auth headers. Use `StaticBearerA2AInboundAuthPolicy` or a
  custom `A2AInboundAuthPolicy` to reject unauthorized inbound A2A peer
  requests at the operation/ASGI boundary. Use `A2ABearerCredential`,
  `RotatingBearerA2ACredentialStore`, `RotatingBearerA2AAuthProvider`, and
  `RotatingBearerA2AInboundAuthPolicy` when bearer credentials need explicit
  overlap windows: the provider selects the current outbound bearer credential,
  and the inbound policy accepts active overlapping inbound bearer credentials
  while rejecting expired, not-yet-valid, revoked, malformed, or unknown tokens
  with a generic unauthorized peer error. The SDK owns this rotation boundary;
  deployments still own credential issuance and secret distribution policy,
  KMS/secret-manager governance, rollout approval, and audit controls. Use
  `HmacA2AJwtVerifier` with `OidcClaimsA2AInboundAuthPolicy` when inbound peer
  auth should validate JWT/OIDC claims for issuer, audience, `exp`, `nbf`, and
  optional peer ids before an A2A operation runs. This JWT/OIDC claims validation
  is a narrow shared-secret JWT boundary. Use
  `OidcDiscoveryMetadataProvider` when deployments need SDK-owned OIDC discovery metadata
  fetch, exact issuer validation, HTTPS `jwks_uri` validation, TTL
  caching, and a stable JWKS URI. Use `JwksA2AJwtVerifier` for RS256/JWKS JWT verification when inbound peer auth should verify public-key JWT signatures through the same
  `OidcClaimsA2AInboundAuthPolicy` claims enforcement boundary. The RS256/JWKS
  JWT verification primitive requires the optional `security` extra. CA trust
  rollout, DNS pinning or egress proxy controls beyond SDK URL policy, tenant
  RBAC mapping, credential issuance, secret distribution, and
  KMS/secret-manager governance remain deployment-owned.
  Use
  `PeerAllowListA2AInboundAuthPolicy` when bearer tokens should identify known
  peer ids and only a configured subset may call the service. Use
  `OperationAllowListA2AInboundAuthPolicy` when different peers should be
  limited to different A2A operation names such as `message/send`,
  `tasks/get`, `tasks/cancel`, or push notification config actions. Use
  `ResourceAllowListA2AInboundAuthPolicy` when peers must be limited to specific
  task ids or push notification config resources. Use `A2ATenantRbacRule` and
  `ClaimsTenantRbacA2AInboundAuthPolicy` when verified JWT/OIDC claims should
  be mapped to tenant, role, and scope attributes before authorizing A2A
  operations or resources. The SDK owns the claims-backed authorization policy
  boundary; tenant directories, role assignment lifecycle, IdP administration,
  credential issuance, secret distribution, and health automation remain
  application or deployment work.
- Use `A2AOperationRateLimitPolicy` with `A2AOperationServer` when public A2A
  operation calls need local throttling after peer authorization and before
  runner/store work. `PeerKeyA2AOperationRateLimitPolicy` combines an
  `A2APeerIdResolver` with the existing channel `RateLimiter` boundary to key
  limits by peer id, operation, task id, and resource context. Denied calls
  return the SDK-owned A2A `-32029` `rate limit exceeded` error via
  `A2ARateLimitError` without exposing bearer tokens, headers, or internal
  limiter keys. Distributed/global quota storage, gateway enforcement,
  billing tiers, and commercial entitlement policy remain deployment-owned.
- Use the A2A Agent Card and `A2AConformanceHarness` as local compatibility
  surfaces, and use `A2AExternalConformanceReportImporter` when CI has already
  run an external conformance suite and needs to attach the result to
  readiness evidence. This external conformance result import is an evidence
  adapter, not a suite runner.
- Use `A2AExternalConformanceInvocationPlan` before CI or release automation
  runs a deployment-owned external conformance suite. The plan records the
  suite id/version, target endpoint, exact command, required checks,
  credential policy reference, network egress policy reference, version matrix
  reference, artifact retention reference, and failure alerting reference.
  `A2AExternalConformanceInvocationGateReport` turns that plan into an
  external conformance invocation gate report for external suite invocation planning
  with JSON-safe readiness metadata and no certification claim.
- Use `A2AExternalConformanceRunner` when code should depend on a narrow SDK
  protocol for external conformance execution, and use
  `A2AExternalConformanceCliRunner` as the external conformance CLI runner
  reference adapter. It executes the invocation plan command as argv-only with
  no shell parsing, can import a report path or stdout JSON, captures bounded stdout/stderr summaries, records timeout/nonzero/import-error evidence, and
  stores only `env_keys` plus redacted output instead of secret values.
- Use `A2AExternalConformanceExecutionProfile` when a deployment needs a
  JSON-safe readiness contract for externally executed A2A conformance. The
  profile gates imported reports against required checks such as `agent-card`,
  `message-send`, `message-stream`, `tasks-resubscribe`, and
  `push-notification-config`, and reports configured execution components:
  `external_suite_runner`, `target_endpoint`, `credential_policy`,
  `network_egress_policy`, `version_matrix`, `ci_artifact_retention`, and
  `failure_alerting`.
- Use `A2AExternalConformanceExecutionRecord` after CI or a deployment-owned
  suite runner, including the SDK reference CLI runner, has executed an
  external conformance suite. The record is the SDK-owned evidence envelope for
  suite name, target endpoint, command, exit code, timestamps, bounded
  stdout/stderr summaries, artifact URI, and imported `A2AConformanceReport`.
  Use `A2AExternalConformanceGateReport` when a
  release gate or readiness probe needs to combine that execution record with
  required-check presence, failed-check projection, configured component
  evidence, JSON-safe metadata, and no certification claim.
- The SDK-owned side is external report import and normalization,
  external conformance invocation plan/gate projection, external conformance
  CLI runner reference execution, external conformance execution record
  normalization, external conformance gate report projection, required-check
  declaration and gating, failed-check projection,
  JSON-safe readiness metadata, and no certification claim. External suite execution, target environment provisioning, credential
  issuance and secret distribution, network egress policy enforcement, DNS pinning, CA trust
  rollout, version matrix execution, CI artifact upload/retention, failure
  alerting and release policy, and certification program or vendor attestation
  remain deployment-owned. External conformance execution records are release
  evidence for local policy and are not proof of certification by themselves.

### `team-discussion`: Team Discussion Agent

Recommended profile: `DistributedTeamRuntimeProfile`

This form covers team records, members, messages, wakeup notices, workspace
handles, `TeamTools`, and a Postgres-backed `TeamStore` for multi-node team
state, plus a `TeamWorkerSessionProvider` boundary for registering independent
worker sessions, `TeamWorkerRunner` for processing team-message continuation
deliveries, `TeamWorkerDaemon` for service-hosted polling, and
`TeamWorkerRetryPolicy` with in-memory or Postgres-backed retry stores for
retry/backoff gating, plus `TeamWorkerCancellationStore` for pre-run
cancellation intents with in-memory or Postgres-backed stores, and
`TeamWorkerPermissionPolicy` for worker workspace and capability downgrade
checks at session creation. It also includes `TeamUiEvent` and
`InMemoryTeamUiStreamStore` as a stable UI event projection protocol,
`PostgresTeamUiStreamStore` for distributed UI event replay, and
`GET /v1/teams/{team_id}/ui-events` plus
`GET /v1/teams/{team_id}/ui-events/stream` for JSON cursor replay and SSE
follow. `DistributedTeamRuntimeProfile` is the SDK preset that assembles these
boundaries into one coherent team runtime, worker runner, optional daemon, and
readiness metadata surface.

Production notes:

- `DistributedTeamRuntimeProfile` is the standard preset for wiring
  `TeamRuntime`, `TeamWorkerRunner`, `TeamWorkerDaemon`, worker session
  provider, retry/cancellation stores, message queue, and UI stream together.
- `TeamRuntime` is the state boundary; worker execution still runs through the
  profile-assembled `TeamWorkerRunner`/`TeamWorkerDaemon`.
- `TeamTools` expose `team_create`, `agent_create`, `team_say`,
  `team_read_messages`, and `team_delete` as LLM-callable operations.
- `TeamWorkerSessionProvider` lets `agent_create` register an independent
  worker session before storing the team member.
- `TeamWorkerRunner` can process queued team-message deliveries and run worker
  continuation turns through an app-provided worker agent resolver. It can also
  use `TeamWorkerRetryPolicy` and a `TeamWorkerRetryStore` to avoid hot-looping
  failed deliveries.
- `TeamWorkerDaemon` can host `TeamWorkerRunner` as a start/stop/join polling
  loop for worker services and exposes current retry records in daemon state.
- Use `WorkerProcessLifecycleDeploymentProfile` when team worker services need
  a JSON-safe `worker_process_lifecycle` readiness contract. Configure
  `process_supervisor`, `restart_policy`, `graceful_shutdown`, `health_probe`,
  `readiness_probe`, `scaling_policy`, `credential_policy`,
  `migration_policy`, `alerting`, and `live_backend_verification` when those
  deployment pieces are present. The SDK-owned side remains
  `TeamWorkerRunner`, `TeamWorkerDaemon`, `TeamWorkerDaemonState`,
  `PlannerRuntime.scheduler_tick`, `PlanSchedulerTickReport`,
  `DistributedTeamRuntimeProfile`, `PlannerOrchestrationDeploymentProfile`,
  `WorkspaceExecutionIsolationProfile`, and readiness-compatible profile
  payloads. The deployment-owned side remains process supervisor or job runner,
  restart policy, graceful shutdown and draining, horizontal scaling policy,
  credentials and secret distribution, schema migration execution, live backend
  verification, health/readiness endpoint wiring, alert routing and runbooks,
  and OS/container sandboxing.
- Use `PostgresTeamStore` plus
  `docs/migrations/2026-06-12-postgres-team-store.sql` when team state must be
  shared across nodes.
- Use `PostgresTeamWorkerRetryStore` plus
  `docs/migrations/2026-06-12-postgres-team-worker-retries.sql` when retry
  state must survive restarts or be visible across nodes.
- Use `InMemoryTeamWorkerCancellationStore` when queued or retry-delayed worker
  continuations need to be skipped before execution in local tests and
  prototypes.
- Use `PostgresTeamWorkerCancellationStore` plus
  `docs/migrations/2026-06-15-postgres-team-worker-cancellations.sql` when
  cancellation intent must survive restarts or be visible across nodes.
- Use `TeamWorkerPermissionPolicy` with `InMemoryTeamWorkerSessionProvider` to
  reject worker workspaces that broaden scope, escape the team workspace root,
  or request capabilities outside a configured allow-list.
- Use `WorkspaceToolSandboxPolicy` with `ToolPathSandboxRule` in the worker
  `ToolCallRouter` to reject workspace path escapes and unauthorized tool
  capabilities before external handlers run. OS/container sandboxing remains
  deployment-owned for tools that execute untrusted code.
- Use `WorkspaceExecutionIsolationProfile` when team worker deployments need
  readiness metadata for execution isolation components without claiming that
  the SDK launches or supervises the sandbox.
- Use `WorkspaceExecutionBackend`, `SandboxBackend`, and
  `WorkspaceExecutionRequest` when a worker needs a swappable Local/Docker/E2B
  or enterprise runner boundary. `LocalWorkspaceExecutionBackend` gives
  argv-only local execution and JSON-safe execution evidence with `env_keys`,
  but it is not a production isolation boundary.
- Use `InMemoryTeamUiStreamStore` to project team lifecycle, membership,
  messages, worker run results, retries, and cancellations into stable UI
  events for local tests and prototypes.
- Use `PostgresTeamUiStreamStore` plus
  `docs/migrations/2026-06-15-postgres-team-ui-events.sql` when UI replay must
  survive restarts or be visible across nodes.
- Use `GET /v1/teams/{team_id}/ui-events` for JSON cursor replay and
  `GET /v1/teams/{team_id}/ui-events/stream` for SSE follow.
- Production deployments still own Redis/Postgres credentials, migration
  execution, process supervision, worker scaling, live backend verification,
  and OS/container sandboxing for untrusted worker tools.

### `planner-intent-router`: Planner / Intent Router Agent

Recommended profile: `DistributedAgentProfile`

This form covers intent-router and plan-and-execute primitives:
`PlannerRuntime`, `PlannerTools`, `PlanStore`, `SubAgentTemplate`,
`PlanDecomposition`, `PlanStepSpec`, `PlanDecompositionGatePolicy`,
`PlanDecompositionGateReport`, `PlannerRuntime.gate_decomposition_proposal`,
`PlanDecompositionValidationReport`, `PlannerRuntime.validate_decomposition`,
`EvidenceHandle`, assignment
boundaries, `PlanRetryPolicy`, step failure/retry metadata,
`PlanDispatchReport`, `PlanSchedulerTickReport`, `PlannerSchedulablePlan`,
`PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`,
`PlanClaimRecord`, `PlanClaimedSchedulerTickReport`,
`PlanClaimedSchedulerTickSkip`, `PlannerClaimedSchedulerDaemon`,
`PlannerClaimedSchedulerDaemonState`, `PlannerClaimedSchedulerDaemonError`,
claimed scheduler daemon polling, `PlannerWorkerDispatchSupervisionProfile`,
`PlanClaimSweepReport`, `PlanClaimSweepSkip`, `PlanClaimSweepStore`,
`PlannerRuntime.sweep_expired_claims`, `PlannerStaleClaimSweepProfile`,
`PlannerSchedulerGovernanceDeploymentProfile`,
`PlannerLlmGovernanceEvidenceRecord`,
`PlannerLlmGovernanceEvidenceGateReport`,
ready-step dispatch through coordinator assignments, schedulable plan selection
through `PlannerRuntime.schedulable_plans` and `plan_schedulable_plans`, plan
claim/lease through `PlannerRuntime.claim_schedulable_plans` and
`plan_claim_schedulable_plans`, the claim-before-tick scheduler boundary through
`PlannerRuntime.claimed_scheduler_tick` and `plan_claimed_scheduler_tick`, the
stale claim sweep boundary through `PlannerRuntime.sweep_expired_claims`, the
`plan_scheduler_tick` one-shot scheduler tick,
planner LLM governance execution evidence through
`PlannerRuntime.gate_llm_governance_evidence`,
`PlannerSchedulerDaemon`, `PlannerSchedulerDaemonState`,
`PostgresPlanStore`, `docs/migrations/2026-06-16-postgres-plan-claims.sql`,
`PlannerDecompositionPolicyDeploymentProfile`,
`PlannerLlmDecompositionGovernanceProfile`,
`PlannerOrchestrationDeploymentProfile`, and compact working-state projection.

Required app glue:

- automatic LLM decomposition policy
- LLM prompt/model/approval/evaluation policy
- plan discovery tenant routing and global fairness policy
- planner scheduler governance profile configuration
- distributed scheduler locks and leader election
- stale claim sweep scheduling policy
- process supervision and restart policy
- compensation orchestration

Production notes:

- `PlanStore` is the truth source. Working state should receive only compact
  projections such as `plan_to_working_state_summary(plan)`.
- Use `PlannerRuntime.gate_decomposition_proposal(...)` or
  `plan_gate_decomposition_proposal` when a leader agent, intent router, or
  app-owned LLM planner has produced a raw JSON-like plan draft. The SDK parses
  and normalizes the proposal, applies `PlanDecompositionGatePolicy` controls
  such as `max_steps`, `require_template`, `allowed_template_ids`, and
  approval-required state, reuses decomposition validation, and returns a
  JSON-safe `PlanDecompositionGateReport` without mutating `PlanStore`.
- Use `plan_create_from_decomposition` or
  `PlannerRuntime.create_plan_from_decomposition(...)` when a leader agent or
  app-owned decomposition policy has produced a structured plan proposal.
- Use `PlannerRuntime.validate_decomposition(...)` before plan creation when an
  LLM or intent-router produces a `PlanDecomposition`. The returned
  `PlanDecompositionValidationReport` is JSON-safe, does not mutate
  `PlanStore`, and reports structural errors, required templates, and unknown
  templates before a proposal becomes persisted planner state.
- Use `PlannerDecompositionPolicyDeploymentProfile` when a deployment needs a
  JSON-safe readiness contract for automatic decomposition policy. Configure
  `prompt_policy`, `output_schema`, `validation_gate`,
  `template_mapping_policy`, `approval_policy`, `model_routing_policy`,
  `evaluation_policy`, `trace_logging`, and `rollback_policy` when those pieces
  are present. The SDK-owned side remains `PlanDecomposition`, `PlanStepSpec`,
  `PlanDecompositionGatePolicy`, `PlanDecompositionGateReport`,
  `PlannerRuntime.gate_decomposition_proposal`,
  `PlannerTools.plan_gate_decomposition_proposal`,
  `PlanDecompositionValidationReport`,
  `PlannerRuntime.validate_decomposition`,
  `PlannerRuntime.create_plan_from_decomposition`,
  `PlannerTools.plan_create_from_decomposition`, `SubAgentTemplate`, and
  objective/step/template/dependency/duplicate-id/cycle validation. The
  deployment-owned side remains intent classification prompt/policy, LLM
  decomposition prompt/policy, model selection and routing, retrieval and
  tool-grounding policy, human approval gate, decomposition evaluation suite,
  cost and latency budgets, rollout and rollback policy, and live backend
  verification.
- Use `PlannerLlmDecompositionGovernanceProfile` when a deployment needs
  governance reference readiness payloads for app-owned LLM decomposition
  controls. Configure `component_refs` for `prompt_policy`,
  `model_routing_policy`, `approval_policy`, `evaluation_policy`,
  `trace_logging`, `rollback_policy`, `output_schema`, `validation_gate`,
  `template_mapping_policy`, `budget_policy`, and
  `live_backend_verification`; attach `evidence_refs` for review docs, CI
  artifacts, eval reports, or release gates. The SDK-owned side is JSON-safe
  governance reference readiness metadata. The deployment-owned side remains
  prompt text and prompt review workflow, model router implementation, human
  approval workflow, evaluation platform execution, budget enforcement,
  rollout/rollback execution, secret distribution, and live backend
  verification execution.
- Use `PlannerLlmGovernanceEvidenceRecord`,
  `PlannerRuntime.gate_llm_governance_evidence`, and
  `PlannerLlmGovernanceEvidenceGateReport` as the per-proposal governance evidence gate
  before production plan creation. The record carries JSON-safe
  prompt/model/approval/evaluation/validation references and pass/fail status,
  and the gate returns `block_plan_creation=True` when required evidence is
  missing or failed. This is planner LLM governance execution evidence only;
  deployment-owned prompt/model/approval/evaluation/validation execution still
  owns the prompt run, model router, approval workflow, evaluation suite,
  schema validator, artifact retention, and certification decision.
- Use `PlanStep.depends_on` and `plan_ready_steps` when an app-owned scheduler
  needs to dispatch only dependency-ready work.
- Use `plan_dispatch_ready_steps` when an app-owned scheduler wants the SDK to
  submit one bounded batch of dependency-ready steps through the existing
  coordinator assignment boundary and receive an auditable dispatch report.
- Use `PlannerRuntime.schedulable_plans(...)` or `plan_schedulable_plans` when
  a deployment-owned scheduler wants an owner-scoped, status-filtered,
  JSON-safe list of plans that currently have ready pending steps or due
  retryable failed steps. The result is a tuple of `PlannerSchedulablePlan`
  summaries with ready step ids, retryable step ids, reasons, and `updated_at`.
  This is schedulable plan selection only.
- Use `PlanClaimStore`, `InMemoryPlanClaimStore`, `PostgresPlanClaimStore`,
  `PlannerRuntime.claim_schedulable_plans(...)`, or
  `plan_claim_schedulable_plans` when a scheduler worker needs an SDK-owned
  plan claim/lease boundary after schedulable-plan selection. The
  result is a JSON-safe `PlanClaimRecord` wrapped in a claim result that reports
  either `claimed` or `busy`. Use
  `docs/migrations/2026-06-16-postgres-plan-claims.sql` with
  `PostgresPlanClaimStore` when claim state must survive restarts or coordinate
  workers across nodes. Production deployments still own plan discovery
  sources, distributed scheduler locks, leader election, stale claim sweep
  scheduling policy, fairness, credentials, migration execution, live backend verification,
  and process supervision.
- Use `PlannerRuntime.claimed_scheduler_tick(...)` or
  `plan_claimed_scheduler_tick` when a scheduler worker needs one SDK-owned
  claim-before-tick scheduler boundary. The helper selects owner/status-filtered
  schedulable plans, claims each plan through the injected `PlanClaimStore`,
  ticks only `claimed` plans, reports busy or failed plans with
  `PlanClaimedSchedulerTickSkip`, returns a `PlanClaimedSchedulerTickReport`,
  and can release the current worker's claims after each tick. It does not
  discover plans outside the supplied owner/status filters and does not provide
  leader election, global fairness, stale-lease sweeping, process supervision,
  tenant authorization, compensation orchestration, credentials, migration
  execution, or live backend verification.
- Use `PlannerClaimedSchedulerDaemon` when a supervised service process needs
  claimed scheduler daemon polling around
  `PlannerRuntime.claimed_scheduler_tick(...)`. `PlannerClaimedSchedulerDaemonState`
  records worker id, lease seconds, owner/status filters, limits, release policy,
  iterations, last `PlanClaimedSchedulerTickReport`, timestamps, and
  `PlannerClaimedSchedulerDaemonError` values. The daemon discovers schedulable
  plans only through the existing owner/status/limit filters and claim store
  boundary; tenant routing, global fairness, distributed scheduler locks, leader
  election, process supervision, stale claim sweep scheduling policy,
  compensation orchestration, credentials, migrations, and live backend
  verification remain deployment-owned.
- Use `PlannerSchedulerGovernanceDeploymentProfile` when a production
  multi-node planner needs a planner scheduler governance profile for
  configured deployment controls. Configure `plan_discovery_policy`,
  `tenant_routing_policy`, `global_fairness_policy`,
  `scheduler_lock_policy`, `leader_election_policy`,
  `stale_lease_recovery_policy`, `worker_dispatch_supervision`, and
  `live_backend_verification` when those controls exist. The SDK-owned side is
  scheduler governance readiness metadata over existing planner primitives;
  the implementation of tenant routing, global fairness, distributed scheduler
  locks, leader election, stale lease recovery, worker dispatch execution,
  compensation orchestration, credentials, migrations, alerting, and live
  backend verification remains deployment-owned.
- Use `PlannerWorkerDispatchSupervisionProfile` when a supervised planner
  worker service needs a planner worker dispatch supervision profile over recent
  `PlanClaimedSchedulerTickReport` history. It emits a dispatch supervision
  payload with claim counts, busy skips, tick-failed skips, released claims,
  consecutive failed batches, `claimed_scheduler_tick_loop` readiness
  component status, and `metrics_alerting` component status. It does not start
  worker processes, discover plans, acquire distributed scheduler locks, sweep
  stale leases, run compensation, issue credentials, run migrations, or verify
  live backends.
- Use `PlannerRuntime.sweep_expired_claims(...)` when a deployment-owned cron,
  scheduler, or operator task needs a stale claim sweep boundary. The SDK scans
  expired `PlanClaimRecord` values through `PlanClaimSweepStore`, supports
  dry-run reports, releases only the exact inspected claim by matching plan id,
  owner, worker, generation, and expiry, and returns a JSON-safe
  `PlanClaimSweepReport` with `PlanClaimSweepSkip` release-race records. Use
  `PlannerStaleClaimSweepProfile` when a readiness endpoint needs a planner stale claim sweep profile over recent reports with
  `stale_claim_sweep_schedule`, `plan_claim_store`, `scheduler_lock_policy`,
  `sweep_safety_window`, `metrics_alerting`, and
  `live_backend_verification` component status. The SDK does not run the cron,
  choose a leader, apply tenant fairness, route alerts, run compensation, issue
  credentials, execute migrations, or verify live backends.
- Use `plan_fail_step`, `plan_retryable_steps`, and `plan_retry_step` when a
  worker or scheduler needs to record failure, wait for retry backoff, and move a
  failed step back to `pending` with attempt metadata preserved.
- Use `plan_scheduler_tick` or `PlannerRuntime.scheduler_tick(...)` when a
  deployment-owned daemon, cron job, or scheduler agent wants one SDK-owned pass
  that first resets due retryable steps and then submits one bounded batch of
  dependency-ready steps. The result is a `PlanSchedulerTickReport` containing
  retry reset metadata plus the normal `PlanDispatchReport`.
- Use `PlannerSchedulerDaemon` when a supervised service process needs a small
  SDK-owned polling loop around `PlannerRuntime.scheduler_tick(...)` for
  explicitly supplied plan ids. `PlannerSchedulerDaemonState` records status,
  configured plan ids, poll interval, iterations, last tick reports, per-plan
  errors, and timestamps. The daemon does not discover plans, acquire
  distributed scheduler locks, supervise processes, manage credentials, run
  migrations, or perform compensation orchestration.
- Use `PlannerOrchestrationDeploymentProfile` when a planner/intent-router
  deployment needs a JSON-safe readiness contract for production orchestration
  components. Configure `decomposition_policy`, `dag_scheduler`,
  `worker_dispatch_loop`, `compensation_policy`, `plan_store`, and
  `worker_supervision` when those pieces are present. The SDK-owned side remains
  `PlannerRuntime`, `PlannerTools`, raw decomposition proposal gating through
  `PlannerRuntime.gate_decomposition_proposal` and
  `plan_gate_decomposition_proposal`, `PlanDecomposition` ingestion,
  dependency-ready step queries, bounded ready-step dispatch, step failure and
  retry metadata, schedulable plan selection through
  `PlannerRuntime.schedulable_plans`, plan claim/lease through
  `PlanClaimStore`, `PostgresPlanClaimStore`, and
  `PlannerRuntime.claim_schedulable_plans`, claim-before-tick scheduler batches
  through `PlannerRuntime.claimed_scheduler_tick`,
  claimed scheduler daemon polling through
  `PlannerClaimedSchedulerDaemon`,
  stale claim sweep reports/releases through
  `PlannerRuntime.sweep_expired_claims` and
  `PlannerStaleClaimSweepProfile`,
  dispatch supervision payloads through
  `PlannerWorkerDispatchSupervisionProfile`,
  `PlannerSchedulerGovernanceDeploymentProfile`,
  `PlannerSchedulerDaemon` local polling over explicitly supplied plan ids,
  the `PlanStore` protocol, and
  working-state summary projection. The deployment-owned side remains automatic
  LLM decomposition policy, tenant routing, global fairness, distributed
  scheduler locks, stale claim sweep scheduling policy, worker dispatch loop execution,
  compensation
  orchestration, worker process lifecycle, process supervision, live backend
  verification, migration execution, credentials and secret distribution, and
  OS/container sandboxing.
- Use `WorkerProcessLifecycleDeploymentProfile` alongside planner orchestration
  probes when planner workers or scheduler services need the same
  `worker_process_lifecycle` deployment contract for `process_supervisor`,
  `restart_policy`, `graceful_shutdown`, `health_probe`, `readiness_probe`,
  `scaling_policy`, `credential_policy`, `migration_policy`, `alerting`, and
  `live_backend_verification`.
- The SDK does not yet provide the LLM prompt/model/approval/evaluation policy
  itself, deployment-owned prompt/model/approval/evaluation/validation execution,
  tenant routing, global fairness, distributed scheduler locks, stale claim sweep
  scheduling policy, process supervision, worker process lifecycle management,
  or complex compensation orchestration.
- Use `WorkspaceExecutionIsolationProfile` when planner-generated subagent work
  needs an explicit workspace isolation readiness contract; the planner profile
  does not replace process/container sandboxing.
- Use `WorkspaceExecutionBackend` or `SandboxBackend` for generated subagent
  execution backends. `WorkspaceExecutionResult` records JSON-safe execution
  evidence, while Docker/E2B/enterprise runner isolation and live verification
  remain deployment-owned.
- Use `PostgresPlanStore` plus
  `docs/migrations/2026-06-15-postgres-plan-store.sql` when plan state must
  survive restarts or be visible across nodes.
- Production deployments still own migration execution, credentials, and rollout
  policy.

## Agent Service Reference Layer

Use `AgentServiceReference` when a production web agent needs a standard
reference service composition over existing SDK boundaries. It builds an
`AsgiAgentApp` from an injected `DistributedWebRuntimeProfile`, adds
`AgentServiceReferenceProfile` readiness through
`AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS`, and wires
`AsgiAgentApp composition`, `DistributedWebRuntimeProfile injection`,
`auth/rate-limit hook injection`, `readiness check aggregation`, and
`JSON-safe readiness evidence` into one reference service.

The Agent Service Reference Layer is not a platform. It does not create Redis,
Postgres, Nacos, Docker, E2B, Kubernetes, systemd, gateways, tenants, or
secrets. Deployment remains responsible for gateway/TLS/CORS/WAF, tenant
directory integration, Kubernetes/systemd/autoscaling, credentials, migrations,
sandbox image patching, alerting, rollout/rollback, and live backend
verification. Use the reference service to show how `AsgiAgentApp`,
`DistributedWebRuntimeProfile`, session snapshot persistence, workspace
backend references, auth/rate-limit hooks, and readiness checks should be
composed without moving deployment ownership into the SDK.
