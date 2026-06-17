# Planner LLM Governance Profile Boundary Design

## Target Conclusion

Production planner and intent-router agents need auditable LLM governance before generated decompositions become plans, but AgentOS should not own prompt text, model routing, approval workflows, evaluation platforms, rollout systems, rollback systems, or live backend checks.

AgentOS should expose a JSON-safe deployment contract that records references to those app-owned policies and evidence artifacts. This makes a deployment reviewable without putting secrets, prompt bodies, provider credentials, or release-system logic inside the SDK.

## Current Gap

`PlannerDecompositionPolicyDeploymentProfile` reports whether broad decomposition-policy components are configured. That is useful for readiness, but production review also needs traceable references for each governance component:

- prompt policy
- model routing policy
- approval policy
- evaluation policy
- trace logging policy
- rollback policy
- output schema
- validation gate
- template mapping policy
- cost/latency budget policy
- live backend verification policy

Without those references, readiness can say a component exists, but an auditor cannot identify the governing document, registry entry, CI artifact, or release gate.

## SDK Boundary

Add `PlannerLlmDecompositionGovernanceProfile` in `agentos.multi.planner`.

The profile owns:

- required governance component names
- configured component detection from non-empty references
- missing component reporting
- JSON-safe readiness metadata
- ASGI-readiness-compatible check payload
- validation of empty names, empty references, and non-serializable metadata

The deployment owns:

- actual prompt templates and prompt review process
- model/router implementation
- human approval UI/workflow
- offline and live evaluation platform
- trace backend
- budget enforcement
- rollout and rollback execution
- live backend verification
- secret storage and credential distribution

## API Shape

`PlannerLlmDecompositionGovernanceProfile` accepts:

- `component_refs: Mapping[str, str]`
- `evidence_refs: Mapping[str, tuple[str, ...]]`
- `metadata: Mapping[str, object]`
- `required_components: tuple[str, ...]`
- `probe_name: str`

It exposes:

- `configured_component_names() -> tuple[str, ...]`
- `missing_components() -> tuple[str, ...]`
- `readiness_metadata() -> dict[str, object]`
- `readiness_check() -> dict[str, object]`

The profile must not execute LLM calls, evaluate prompts, contact model providers, call approval systems, or verify live backends. It is a boundary and audit payload only.

## Integration

Export the profile through:

- `agentos.multi`
- top-level `agentos`

Update readiness, production docs, objective audit, roadmap, and SDK skill guidance so planner/intent-router guidance points developers to this profile for governance references while keeping actual governance deployment-owned.

## Non-Goals

- No automatic LLM decomposition policy implementation.
- No prompt content storage.
- No model-router implementation.
- No approval UI.
- No evaluation runner.
- No live backend probe execution.
- No changes to `runtime/query_loop.py` or `runtime/async_query_loop.py`.
