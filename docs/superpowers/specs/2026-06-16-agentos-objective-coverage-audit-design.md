# AgentOS Objective Coverage Audit Design

## Target Conclusion

The SDK has accumulated enough terminal, web, A2A, team, planner, and workspace
primitives that the main risk is no longer a single missing module. The risk is
losing the original objective across dozens of phases. AgentOS needs a
versioned objective coverage audit that maps each user-requested agent shape to
current SDK evidence, readiness level, and remaining production work.

## Current State

Current evidence is split across:

- `agentos.readiness`
- `docs/production-readiness.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

These sources are accurate but hard to use when answering completion
percentage, remaining blockers, or whether a future phase is still aligned with
the original goal.

## Proposed Boundary

Add `docs/agentos-objective-coverage-audit.md` as the human-facing coverage
ledger for the original objective.

The audit must include:

- scope and scoring definitions
- high-level completion percentage
- one row for each original objective area:
  - terminal/script agents
  - single-node async web agents
  - distributed web dynamic context hydration
  - AgentScope2/A2A card, registry, and discovery
  - A2A operation interaction
  - multi-agent team discussion
  - sync versus async loop guidance
  - planner / plan-and-execute
  - main-agent intent routing with subagent execution
  - workspace layer expansion
  - SDK developer skill guidance
- evidence pointers to current SDK/docs artifacts
- remaining production blockers
- next phase priorities

## Non-Goals

- No runtime behavior changes.
- No new production profile.
- No new query-loop coupling.
- No attempt to declare the whole long-running goal complete.
- No external conformance execution.

## Validation

Add docs tests that require the audit to contain every objective area, the key
evidence artifacts, the major remaining blockers, and a Phase 67 roadmap entry.

