# AgentOS Release Scope

This document is the release boundary for Phase 96: Release Scope Re-baseline.
It is the canonical release scope re-baseline for the first production SDK
release and should be read together with `docs/production-readiness.md` and
`docs/agentos-objective-coverage-audit.md`.

Canonical path: `docs/release-scope.md`.

## Target Conclusion

AgentOS is a company-level agent SDK and development skill, not an all-in-one
AgentScope-style platform. The first production SDK release supports trusted
tools, internal service orchestration, terminal agent builds, single-node web
agent builds, distributed web agent builds, team/planner/A2A primitive
composition, production state plane boundaries, and readiness evidence.

Sandbox / Docker / E2B / microVM / enterprise runner adapter work is a
non-blocking future adapter path, not a release blocker. The release does not
promise physical isolation for untrusted code execution.

## Release-In Scope

- Terminal and script agents using `LocalRuntimeProfile`, `AgentBuilder`, sync
  `QueryLoop`, async loop support where useful, and trusted tools.
- Single-node web agents using `WebRuntimeProfile`, `AsyncQueryLoop`,
  `AsgiAgentApp`, and app-owned auth/rate-limit/workspace policy.
- Distributed web agents using `DistributedWebRuntimeProfile`,
  durable session hydration, Redis lease, Postgres session snapshots, and
  readiness evidence for acquire/hydrate/save/release lifecycle.
- Production state plane boundaries: `NacosAgentRegistryAdapter` for
  AgentCard discovery metadata, `RedisAgentMessageQueue` for delivery/wakeup,
  `PostgresTaskStore` and `PostgresPlanStore` for truth state,
  `WorkerProcessSupervisor` for lifecycle evidence, and
  `SessionSnapshotPersistence` for runtime snapshots.
- Team/planner/A2A primitive support for team discussions, intent routing,
  plan-and-execute, AgentCard discovery, internal A2A task bridges, and
  external conformance evidence boundaries.
- Release gates through `ProductionReadinessEvidenceBundle`,
  `ReadinessEvidenceCheck`, `ReadinessEvidenceStatus`, live backend verification
  records, JSON-safe readiness evidence, and audit evidence.

## Release-Out Scope

The SDK does not own Kubernetes, systemd, secret distribution, autoscaling,
tenant directory integration, CI matrix execution, production rollout/rollback,
alert routing, runbooks, sandbox image patching, or true production isolation.

The SDK also does not own physical isolation for untrusted code execution.
Sandbox / Docker / E2B / microVM / enterprise runner adapter support remains a
future adapter lane. The current release keeps only the
`WorkspaceExecutionBackend` / `SandboxBackend` protocol boundary,
`LocalWorkspaceExecutionBackend` local reference backend,
policy/capability/path pre-check, JSON-safe execution evidence, and audit
evidence. That sandbox posture must be explicit in generated specs:
`trusted tools only`, `deployment-owned isolation`, or `future adapter`.

## Sandbox Posture

Every production-bound spec must choose a sandbox posture:

- `trusted tools only`: acceptable for internal tools and internal service
  orchestration where tool handlers run in a trusted deployment boundary.
- `deployment-owned isolation`: required when the deployment provides Docker,
  E2B, microVM, enterprise runner, Kubernetes, systemd, or another isolation
  layer outside the SDK.
- `future adapter`: acceptable when untrusted execution is not part of this
  release and the spec records the missing adapter as future work.

`WorkspaceToolSandboxPolicy`, `ToolPathSandboxRule`,
`WorkspaceExecutionBackend`, `SandboxBackend`, `WorkspaceExecutionRequest`,
`WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, and
`LocalWorkspaceExecutionBackend` are SDK boundaries. They do not make untrusted
code safe by themselves.

## Release Gate

A release candidate should be considered in scope only when:

- `docs/production-readiness.md` links this file and names the first production
  SDK release boundary.
- `docs/agentos-objective-coverage-audit.md` keeps the goal active while naming
  remaining deployment-owned work.
- `.claude/skills/agent-os` spec generation requires agent form, runtime
  profile, state plane, readiness evidence, release gate evidence, and sandbox
  posture decisions.
- Runtime boundary scans show planner/A2A/team/worker/state-plane/readiness and
  sandbox concepts have not been moved into `QueryLoop` or `AsyncQueryLoop`.
