# SDK Skill / Spec Generator Finalization Design

## Target Conclusion

Phase 99: SDK Skill / Spec Generator Finalization should make AgentOS a
production agent design constraint generator. The SDK skill should not merely
explain runtime modules; it should force every production-bound agent spec to
make the architecture choices that determine whether terminal, web,
distributed web, A2A, team, planner, and readiness shapes are actually
deployable.

## Boundary

AgentOS owns a spec generator finalization gate and an SDK-owned constraint
template named `production_design_constraints`. The template must explicitly
choose agent form, runtime profile, state plane components, persistence
backend, registry backend, queue backend, worker supervisor, A2A exposure,
planner/team mode, production readiness checklist, and sandbox posture: trusted
tools only | deployment-owned isolation | future adapter.

AgentOS does not create deployment-owned infrastructure. Concrete Nacos, Redis,
Postgres, worker supervisor, Kubernetes/systemd/autoscaling, tenant directory,
CI/CD, credentials, migrations, backend probe execution, and physical isolation
remain deployment-owned.

## Design

The finalization gate lives in the skill and docs rather than in `QueryLoop` or
`AsyncQueryLoop`. Requirements gathering collects the required decisions.
Spec generation writes them into `deployment.production_design_constraints`.
Implementation guidance refuses to proceed when the block is missing for a
production-bound spec.

The generated block is not a deployment manifest. It is a production design
contract that tells reviewers which SDK primitives are used, which deployment
responsibilities remain outside the SDK, and which readiness evidence must be
present before release.

## Acceptance

- Production readiness documents Phase 99.
- The objective coverage audit records Overall completion estimate: 99%.
- The roadmap links this design and its implementation plan.
- `.claude/skills/agent-os/SKILL.md` describes the finalization gate.
- `flow/01-requirements.md` gathers all required choices.
- `flow/02-spec-generation.md` emits `production_design_constraints`.
- `flow/03-implementation.md` blocks implementation handoff when the block is
  missing.
- Tests prove the same required phrases appear in production readiness, audit,
  roadmap, skill, requirements, spec generation, and implementation guidance.
