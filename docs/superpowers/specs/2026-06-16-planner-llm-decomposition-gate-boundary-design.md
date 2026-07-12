# Planner LLM Decomposition Gate Boundary Design

> Phase: 80

## Target Conclusion

AgentOS should not own automatic LLM planning, prompt policy, model routing, or
human approval workflow. It should provide a narrow SDK gate for raw
LLM-produced decomposition proposals so leader agents can parse, normalize,
validate, and audit a JSON-like plan draft before persisting it as a
`PlanDecomposition`.

## Problem

`PlannerRuntime.validate_decomposition(...)` validates an already constructed
`PlanDecomposition`. Production intent-router agents still need a safer entry
point for model output that may be malformed, over-sized, missing required
fields, or require human approval before subagent execution.

Without this boundary, applications must reimplement the same parser and audit
shape around every LLM planner prompt.

## Scope

Add an SDK-owned gate that:

- accepts a mapping shaped like an LLM or main-agent JSON proposal;
- parses `objective`, `steps`, `step_id`, `instruction`,
  `required_capabilities`, `template_id`, and `depends_on`;
- returns JSON-safe reports for parse errors, validation errors, unknown
  templates, max-step violations, and approval-required policy;
- exposes the normalized `PlanDecomposition` only when accepted;
- offers a `PlannerTools` read-only tool so leader agents can gate proposals
  before calling `plan_create_from_decomposition`.

## Out Of Scope

- Calling an LLM or generating the decomposition prompt.
- Selecting models, retrieval context, or cost/latency policy.
- Running human approval UIs or release workflows.
- Creating a plan automatically from the gate result.
- Adding planner concepts to `QueryLoop` or `AsyncQueryLoop`.

## Proposed API

- `PlanDecompositionGatePolicy`
  - `max_steps`
  - `require_template`
  - `require_approval`
  - `approved`
  - `allowed_template_ids`
- `PlanDecompositionGateReport`
  - `accepted`
  - `requires_approval`
  - `errors`
  - `validation`
  - `step_count`
  - `required_templates`
  - `unknown_templates`
  - `missing_templates`
  - `disallowed_templates`
  - `normalized_decomposition`
  - `metadata`
- `PlannerRuntime.gate_decomposition_proposal(...)`
- `PlannerTools` tool: `plan_gate_decomposition_proposal`

## Data Flow

```text
leader agent / LLM JSON
  -> PlannerRuntime.gate_decomposition_proposal(...)
  -> parse raw mapping into PlanDecomposition
  -> apply gate policy
  -> PlannerRuntime.validate_decomposition(...)
  -> return PlanDecompositionGateReport
  -> app decides whether to approve and call plan_create_from_decomposition
```

## Boundaries

SDK-owned:

- JSON-like proposal parsing and normalization.
- Step/template/dependency validation reuse.
- Deterministic JSON-safe gate reports.
- Read-only planner tool exposure.

Deployment-owned:

- prompt, model, retrieval, approval UI, evaluation suite, rollout, rollback,
  live backend verification, plan discovery, distributed locks, worker process
  lifecycle, compensation, credentials, and sandboxing.

## Acceptance Criteria

- Valid raw proposals produce accepted reports with a normalized decomposition.
- Malformed proposals return rejected reports without mutating `PlanStore`.
- `require_template`, `allowed_template_ids`, and `max_steps` are enforced.
- Approval-required policy blocks acceptance until `approved=True`.
- `PlannerTools` exposes a read-only gate tool.
- Public API exports include the new policy and report types.
- Readiness/docs/skill/audit/roadmap mention the new gate without claiming
  automatic LLM planning is SDK-owned.
- Runtime query loops remain free of planner/A2A/team imports and terms.
