# Planner Decomposition Policy Boundary Design

## Target Conclusion

Planner and intent-router agents need an SDK-owned gate for LLM-produced plan
decompositions, but AgentOS should not own the prompt, model choice, autonomous
planning loop, or human-approval policy. The SDK should validate structured
`PlanDecomposition` proposals before they become persisted `PlanState` records
and should expose readiness metadata for the deployment policies that govern
automatic LLM decomposition.

## Current State

The SDK already owns useful planner primitives:

- `PlanDecomposition`
- `PlanStepSpec`
- `PlannerRuntime.create_plan_from_decomposition(...)`
- `PlannerTools.plan_create_from_decomposition`
- `SubAgentTemplate`
- dependency-aware step validation
- `PlannerOrchestrationDeploymentProfile`

The remaining gap is that an app-owned LLM planner can emit a structured
decomposition, but deployments do not have a first-class SDK validation report
or readiness profile that separates "this decomposition is structurally safe to
ingest" from "this LLM prompt/policy is production-approved."

## Proposed Boundary

Add two narrow SDK boundaries in `agentos.multi.planner`.

`PlanDecompositionValidationReport`:

- validates `PlanDecomposition` without creating a plan
- returns JSON-safe metadata through `as_dict()`
- reports `ok`, `errors`, `step_count`, `required_templates`, and
  `unknown_templates`
- reuses the same objective, step, template, dependency, duplicate-id, and
  cycle rules as `create_plan_from_decomposition(...)`

`PlannerRuntime.validate_decomposition(...)`:

- accepts a `PlanDecomposition`
- returns `PlanDecompositionValidationReport`
- does not mutate `PlanStore`
- can be used by a leader agent or app-owned LLM planner before calling
  `create_plan_from_decomposition(...)`

`PlannerDecompositionPolicyDeploymentProfile`:

- accepts configured deployment component names
- reports missing required decomposition-policy components
- exposes `readiness_metadata()`
- exposes ASGI-compatible `readiness_check()`
- returns JSON-safe tuples/booleans/strings
- documents SDK-owned and deployment-owned responsibilities

Required components:

- `prompt_policy`
- `output_schema`
- `validation_gate`
- `template_mapping_policy`
- `approval_policy`
- `model_routing_policy`
- `evaluation_policy`
- `trace_logging`
- `rollback_policy`

SDK-owned responsibilities:

- `PlanDecomposition`
- `PlanStepSpec`
- `PlanDecompositionValidationReport`
- `PlannerRuntime.validate_decomposition`
- `PlannerRuntime.create_plan_from_decomposition`
- `PlannerTools.plan_create_from_decomposition`
- `SubAgentTemplate`
- objective, step, template, dependency, duplicate-id, and cycle validation

Deployment-owned responsibilities:

- intent classification prompt/policy
- LLM decomposition prompt/policy
- model selection and routing
- retrieval and tool-grounding policy
- human approval gate
- decomposition evaluation suite
- cost and latency budgets
- rollout and rollback policy
- live backend verification

## Non-Goals

- No automatic LLM planner.
- No prompt templates.
- No model routing or provider calls.
- No human-approval UI.
- No autonomous plan execution loop.
- No changes to `QueryLoop` or `AsyncQueryLoop`.
- No new persistent schema.

## Validation

- Planner runtime tests prove validation reports, no-store-mutation behavior,
  profile readiness metadata, and profile input validation.
- Public API tests prove exports from `agentos.multi` and top-level `agentos`.
- Readiness/docs tests prove planner/intent-router guidance names the validation
  boundary and keeps automatic LLM policy deployment-owned.
- Objective coverage audit moves automatic decomposition from an unbounded gap
  to an SDK validation/profile boundary plus deployment-owned policy.
- Runtime boundary scan proves planner concepts do not leak into query loops.
