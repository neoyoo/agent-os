from __future__ import annotations

from tests.planning._async import async_test


import pytest

from agentos.planning import (
    InMemoryPlanStore,
    PlanDecompositionGatePolicy,
    PlannerLlmGovernanceEvidenceRecord,
    PlannerRuntime,
    SubAgentTemplate,
)


@async_test
async def test_planner_runtime_gates_valid_raw_decomposition_proposal() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review findings.",
            ),
        ),
    )

    report = runtime.gate_decomposition_proposal(
        {
            "objective": "Review AgentOS planner architecture.",
            "steps": [
                {
                    "step_id": "collect",
                    "instruction": "Collect planner requirements.",
                    "required_capabilities": ["research"],
                    "template_id": "researcher",
                },
                {
                    "step_id": "review",
                    "instruction": "Review planner risks.",
                    "required_capabilities": ["review"],
                    "template_id": "reviewer",
                    "depends_on": ["collect"],
                },
            ],
        },
    )

    assert report.accepted is True
    assert report.requires_approval is False
    assert report.errors == ()
    assert report.step_count == 2
    assert report.required_templates == ("researcher", "reviewer")
    assert report.unknown_templates == ()
    assert report.missing_templates == ()
    assert report.disallowed_templates == ()
    assert report.validation is not None
    assert report.validation.ok is True
    assert report.normalized_decomposition is not None
    assert report.normalized_decomposition.objective == (
        "Review AgentOS planner architecture."
    )
    assert report.normalized_decomposition.steps[1].depends_on == ("collect",)
    assert report.as_dict()["normalized_decomposition"] == {
        "objective": "Review AgentOS planner architecture.",
        "steps": [
            {
                "step_id": "collect",
                "instruction": "Collect planner requirements.",
                "required_capabilities": ("research",),
                "template_id": "researcher",
                "depends_on": (),
            },
            {
                "step_id": "review",
                "instruction": "Review planner risks.",
                "required_capabilities": ("review",),
                "template_id": "reviewer",
                "depends_on": ("collect",),
            },
        ],
    }
    assert await runtime.list_plans() == []


@async_test
async def test_planner_runtime_gate_rejects_malformed_raw_proposal_without_mutation() -> (
    None
):
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    report = runtime.gate_decomposition_proposal(
        {
            "objective": "Malformed plan.",
            "steps": [
                {
                    "instruction": "Valid step.",
                },
                "not an object",
            ],
        },
    )

    assert report.accepted is False
    assert report.validation is None
    assert report.normalized_decomposition is None
    assert report.step_count == 0
    assert report.errors == ("step 2 must be an object",)
    assert report.as_dict()["accepted"] is False
    assert await runtime.list_plans() == []


@async_test
async def test_planner_runtime_gate_applies_template_policy_and_limits() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review findings.",
            ),
        ),
    )

    report = runtime.gate_decomposition_proposal(
        {
            "objective": "Review AgentOS planner architecture.",
            "steps": [
                {
                    "step_id": "collect",
                    "instruction": "Collect planner requirements.",
                    "template_id": "researcher",
                },
                {
                    "step_id": "write",
                    "instruction": "Write summary.",
                },
                {
                    "step_id": "review",
                    "instruction": "Review planner risks.",
                    "template_id": "reviewer",
                },
            ],
        },
        policy=PlanDecompositionGatePolicy(
            max_steps=2,
            require_template=True,
            allowed_template_ids=("researcher",),
        ),
    )

    assert report.accepted is False
    assert report.step_count == 3
    assert report.missing_templates == ("write",)
    assert report.disallowed_templates == ("reviewer",)
    assert "decomposition exceeds max_steps: 3 > 2" in report.errors
    assert "step write requires a template_id" in report.errors
    assert "template not allowed: reviewer" in report.errors
    assert report.validation is not None
    assert report.validation.ok is True
    assert await runtime.list_plans() == []


@async_test
async def test_planner_runtime_gate_blocks_until_required_approval_is_present() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())
    proposal = {
        "objective": "Review AgentOS planner architecture.",
        "steps": [
            {
                "instruction": "Collect planner requirements.",
            },
        ],
    }

    blocked = runtime.gate_decomposition_proposal(
        proposal,
        policy=PlanDecompositionGatePolicy(require_approval=True),
    )
    approved = runtime.gate_decomposition_proposal(
        proposal,
        policy=PlanDecompositionGatePolicy(
            require_approval=True,
            approved=True,
        ),
    )

    assert blocked.accepted is False
    assert blocked.requires_approval is True
    assert blocked.errors == ("decomposition approval is required",)
    assert blocked.as_dict()["requires_approval"] is True
    assert approved.accepted is True
    assert approved.requires_approval is False
    assert approved.errors == ()
    assert await runtime.list_plans() == []


@async_test
async def test_planner_llm_governance_evidence_gate_accepts_complete_external_evidence() -> (
    None
):

    runtime = PlannerRuntime(store=InMemoryPlanStore())
    record = PlannerLlmGovernanceEvidenceRecord(
        proposal_id="proposal_123",
        objective="Review AgentOS planner architecture.",
        prompt_ref="prompt://planner/decomposition/v4",
        prompt_hash="sha256:prompt-v4",
        model_ref="model-router://planner/decomposition",
        model_version="gpt-5.1-planner-2026-06-16",
        approval_ref="approval://planner/decomposition/approved-123",
        approved=True,
        evaluation_ref="eval://planner/decomposition/report-456",
        evaluation_passed=True,
        validation_ref="schema://planner/decomposition/validation-789",
        validation_passed=True,
        output_schema_ref="schema://planner/decomposition/v1",
        trace_ref="trace://planner/decomposition/run-123",
        budget_ref="budget://planner/decomposition/default",
        metadata={
            "release": "planner-decomposition-v4",
            "tenant": "internal-platform",
        },
    )

    report = runtime.gate_llm_governance_evidence(record)

    assert report.accepted is True
    assert report.block_plan_creation is False
    assert report.errors == ()
    assert report.missing_evidence == ()
    assert report.failed_evidence == ()
    assert report.record == record
    assert report.as_dict() == {
        "accepted": True,
        "block_plan_creation": False,
        "errors": (),
        "missing_evidence": (),
        "failed_evidence": (),
        "record": record.as_dict(),
        "metadata": {},
    }
    assert record.as_dict()["prompt_ref"] == "prompt://planner/decomposition/v4"
    assert record.as_dict()["metadata"] == {
        "release": "planner-decomposition-v4",
        "tenant": "internal-platform",
    }
    assert await runtime.list_plans() == []


@async_test
async def test_planner_llm_governance_evidence_gate_blocks_missing_approval_evidence() -> (
    None
):

    runtime = PlannerRuntime(store=InMemoryPlanStore())
    record = PlannerLlmGovernanceEvidenceRecord(
        proposal_id="proposal_123",
        objective="Review AgentOS planner architecture.",
        prompt_ref="prompt://planner/decomposition/v4",
        model_ref="model-router://planner/decomposition",
        evaluation_ref="eval://planner/decomposition/report-456",
        evaluation_passed=True,
        validation_ref="schema://planner/decomposition/validation-789",
        validation_passed=True,
    )

    report = runtime.gate_llm_governance_evidence(record)

    assert report.accepted is False
    assert report.block_plan_creation is True
    assert report.missing_evidence == ("approval_evidence",)
    assert report.failed_evidence == ()
    assert report.errors == ("approval evidence is required",)
    assert report.as_dict()["block_plan_creation"] is True
    assert await runtime.list_plans() == []


@async_test
async def test_planner_llm_governance_evidence_gate_blocks_failed_evaluation() -> None:

    runtime = PlannerRuntime(store=InMemoryPlanStore())
    record = PlannerLlmGovernanceEvidenceRecord(
        proposal_id="proposal_123",
        objective="Review AgentOS planner architecture.",
        prompt_ref="prompt://planner/decomposition/v4",
        model_ref="model-router://planner/decomposition",
        approval_ref="approval://planner/decomposition/approved-123",
        approved=True,
        evaluation_ref="eval://planner/decomposition/report-456",
        evaluation_passed=False,
        validation_ref="schema://planner/decomposition/validation-789",
        validation_passed=True,
    )

    report = runtime.gate_llm_governance_evidence(record)

    assert report.accepted is False
    assert report.block_plan_creation is True
    assert report.missing_evidence == ()
    assert report.failed_evidence == ("evaluation_evidence",)
    assert report.errors == ("evaluation evidence did not pass",)
    assert await runtime.list_plans() == []


@async_test
async def test_planner_llm_governance_evidence_record_rejects_invalid_refs_and_metadata() -> (
    None
):

    with pytest.raises(ValueError, match="proposal_id"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id=" ",
            objective="Review AgentOS planner architecture.",
        )
    with pytest.raises(ValueError, match="objective"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective=" ",
        )
    with pytest.raises(ValueError, match="prompt_ref"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective="Review AgentOS planner architecture.",
            prompt_ref=" ",
        )
    with pytest.raises(ValueError, match="metadata"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective="Review AgentOS planner architecture.",
            metadata={"not_json": object()},
        )
    with pytest.raises(ValueError, match="metadata"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective="Review AgentOS planner architecture.",
            metadata={"secret_token": "sk-test"},
        )


@async_test
async def test_planner_llm_governance_evidence_payload_omits_raw_prompt_and_secrets() -> (
    None
):

    with pytest.raises(ValueError, match="metadata"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective="Review AgentOS planner architecture.",
            metadata={"raw_prompt": "Create an internal production plan."},
        )

    record = PlannerLlmGovernanceEvidenceRecord(
        proposal_id="proposal_123",
        objective="Review AgentOS planner architecture.",
        prompt_ref="prompt://planner/decomposition/v4",
        prompt_hash="sha256:prompt-v4",
        model_ref="model-router://planner/decomposition",
        model_version="gpt-5.1-planner-2026-06-16",
        approval_ref="approval://planner/decomposition/approved-123",
        approved=True,
        evaluation_ref="eval://planner/decomposition/report-456",
        evaluation_passed=True,
        validation_ref="schema://planner/decomposition/validation-789",
        validation_passed=True,
        metadata={"release": "planner-decomposition-v4"},
    )
    payload = record.as_dict()

    serialized = str(payload)
    assert "raw_prompt" not in serialized
    assert "Create an internal production plan." not in serialized
    assert "sk-test" not in serialized
    assert payload["prompt_ref"] == "prompt://planner/decomposition/v4"
