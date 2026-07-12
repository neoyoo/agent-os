# Release Scope Re-baseline Design

## Target Conclusion

Phase 96: Release Scope Re-baseline defines the first production SDK release as
a boundary-first SDK release, not a full platform release. The release supports
trusted tools, internal service orchestration, terminal agent, single-node web
agent, distributed web agent, team/planner/A2A primitive composition,
production state plane, readiness evidence, and audit evidence. Sandbox /
Docker / E2B / microVM / enterprise runner adapter work is a non-blocking
future adapter and not a release blocker; AgentOS does not promise physical
isolation for untrusted code execution in this release.

## Problem

The architecture review added many primitives and adapter boundaries. Without a
release scope re-baseline, the project can drift back into an endless platform
build where Sandbox, Docker, E2B, microVM, Kubernetes, systemd, autoscaling, and
tenant operations all appear to block the SDK. The SDK needs a release boundary
that is explicit, repeatable, and testable.

## SDK-Owned Boundary

- Stable protocols and profiles.
- Agent form guidance for terminal agent, single-node web agent, distributed
  web agent, team/planner/A2A primitive forms.
- Production state plane boundaries for registry, queue, task truth, plan
  truth, worker lifecycle evidence, and session snapshots.
- Readiness evidence and release gate evidence through
  `ProductionReadinessEvidenceBundle`.
- `WorkspaceExecutionBackend`, `SandboxBackend`, and
  `LocalWorkspaceExecutionBackend` as protocol/reference boundaries.
- policy/capability/path pre-check through workspace policy primitives.
- JSON-safe audit evidence for readiness and local reference execution.

## Deployment-Owned Boundary

- Kubernetes, systemd, secret distribution, autoscaling, tenant directory,
  CI/CD, rollout/rollback, alert routing, and runbooks.
- Real Nacos/Redis/Postgres credentials, migrations, and live backend probes.
- Docker/E2B/microVM/enterprise runner implementation and sandbox image
  patching.
- Physical isolation for untrusted code execution.

## Sandbox Posture

Every production-bound spec must select one sandbox posture:

- `trusted tools only`
- `deployment-owned isolation`
- `future adapter`

The sandbox posture must state that Sandbox / Docker / E2B / microVM /
enterprise runner adapter support is a non-blocking future adapter unless a
deployment-owned isolation layer is provided.

## Documentation Surfaces

- `docs/release-scope.md`
- `docs/production-readiness.md`
- `docs/agentos-objective-coverage-audit.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- `.claude/skills/agent-os/SKILL.md`
- `.claude/skills/agent-os/flow/01-requirements.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/architecture.md`
- `.claude/skills/agent-os/modules/multi-agent.md`

## Success Criteria

- Tests require the Phase 96 release boundary across production readiness,
  objective audit, roadmap, and skill guidance.
- The release scope document states that Sandbox / Docker / E2B / microVM /
  enterprise runner adapter work is not a release blocker.
- The SDK skill requires a sandbox posture decision in requirements and spec
  generation.
- Runtime loops remain free of planner/A2A/team/worker/state-plane/readiness
  and sandbox concepts.
