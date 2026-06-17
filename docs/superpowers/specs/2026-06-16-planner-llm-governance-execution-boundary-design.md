# Planner LLM Governance Execution Boundary Design

> Phase: 92

## Target Conclusion

Production planner and intent-router agents may use LLM-generated plan
decompositions, but production plan creation must be blocked unless the
deployment supplies auditable prompt, model, approval, evaluation, and
validation evidence for that specific decomposition proposal.

AgentOS should not execute prompts, call model providers, run approval
workflows, run evaluation suites, store prompt bodies, or certify governance
itself. The SDK boundary is to consume JSON-safe references and pass/fail
statuses from deployment-owned systems, then return a deterministic gate report
that can be checked before `create_plan_from_decomposition(...)`.

## Current Gap

The existing planner boundary has two useful layers:

- `PlannerRuntime.gate_decomposition_proposal(...)` parses and validates raw
  LLM/main-agent JSON proposals without mutating `PlanStore`.
- `PlannerLlmDecompositionGovernanceProfile` reports whether deployment-owned
  governance references are configured for prompt policy, model routing,
  approval policy, evaluation policy, output schema, validation gate, budget,
  and live backend verification.

Those layers do not yet prove that a particular LLM decomposition proposal has
execution evidence. A deployment can be generally configured, but an individual
proposal may still lack model routing evidence, approval evidence, evaluation
evidence, or validation evidence. That gap lets an app accidentally persist an
LLM plan that was not approved or evaluated for production.

## SDK Boundary

Add a narrow per-proposal evidence boundary in `agentos.multi.planner`:

- `PlannerLlmGovernanceEvidenceRecord`
- `PlannerLlmGovernanceEvidenceGateReport`
- `PlannerRuntime.gate_llm_governance_evidence(...)`

The record owns only references, hashes, statuses, and JSON-safe metadata. It
must not contain raw prompt text, provider credentials, secret values, or full
model output bodies.

The gate owns deterministic validation:

- required evidence references are present;
- approval status is present and approved when approval evidence is required;
- evaluation status is present and passed when evaluation evidence is required;
- validation status is present and passed when validation evidence is required;
- JSON-safe metadata is preserved;
- plan creation should be blocked when evidence is missing or failed.

The gate does not persist plans and does not mutate `PlanStore`.

## Deployment Boundary

The deployment owns:

- prompt template storage and prompt review;
- prompt execution and provider request construction;
- model routing and model/provider versioning;
- approval workflow, approver identity, and authorization policy;
- evaluation suite execution and result artifacts;
- schema validation implementation;
- trace backend and artifact retention;
- cost/latency budget enforcement;
- live backend verification;
- release/certification decision.

AgentOS only consumes references and pass/fail evidence from those systems.

## API Shape

`PlannerLlmGovernanceEvidenceRecord` accepts:

- `proposal_id: str`
- `objective: str`
- `prompt_ref: str | None`
- `prompt_hash: str | None`
- `model_ref: str | None`
- `model_version: str | None`
- `approval_ref: str | None`
- `approved: bool | None`
- `evaluation_ref: str | None`
- `evaluation_passed: bool | None`
- `validation_ref: str | None`
- `validation_passed: bool | None`
- `output_schema_ref: str | None`
- `trace_ref: str | None`
- `budget_ref: str | None`
- `metadata: Mapping[str, object]`

It exposes `as_dict() -> dict[str, object]`.

`PlannerLlmGovernanceEvidenceGateReport` returns:

- `accepted: bool`
- `block_plan_creation: bool`
- `errors: tuple[str, ...]`
- `missing_evidence: tuple[str, ...]`
- `failed_evidence: tuple[str, ...]`
- `record: PlannerLlmGovernanceEvidenceRecord`
- `metadata: Mapping[str, object]`

It exposes `as_dict() -> dict[str, object]`.

`PlannerRuntime.gate_llm_governance_evidence(...)` accepts a record and optional
required-evidence override. It returns the gate report and never creates a plan.

## Data Flow

```text
deployment-owned LLM planner execution
  -> prompt/model/approval/eval/validation systems produce refs and statuses
  -> app builds PlannerLlmGovernanceEvidenceRecord
  -> PlannerRuntime.gate_llm_governance_evidence(...)
  -> accepted report allows app to call create_plan_from_decomposition(...)
  -> rejected report blocks production plan creation
```

## Acceptance Criteria

- A record with prompt/model/approval/evaluation/validation evidence and passing
  statuses produces `accepted=True` and `block_plan_creation=False`.
- Missing prompt, model, approval, evaluation, or validation evidence produces
  a rejected report with deterministic `missing_evidence`.
- Failed approval/evaluation/validation statuses produce a rejected report with
  deterministic `failed_evidence`.
- Record and report payloads are JSON-safe and omit raw prompt text and secrets.
- Invalid empty references and non-JSON metadata are rejected.
- Public API exports include the record and report.
- Production readiness docs, objective audit, roadmap, and agent-os skill
  guidance describe the boundary and keep execution platforms deployment-owned.
- `runtime/query_loop.py` and `runtime/async_query_loop.py` remain free of
  planner governance concepts.

## Non-Goals

- No prompt execution.
- No provider/model call.
- No prompt registry.
- No approval UI or authorization system.
- No evaluation runner.
- No certification claim.
- No automatic plan creation from a gate report.
- No changes to `QueryLoop` or `AsyncQueryLoop`.
