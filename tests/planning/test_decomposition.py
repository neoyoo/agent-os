import pytest

from agentos.planning import (
    PlanDecomposition,
    PlanDecompositionGatePolicy,
    PlannerLlmGovernanceEvidenceRecord,
    PlanStepSpec,
    SubAgentTemplate,
)
from agentos.planning.decomposition import (
    gate_decomposition_proposal,
    materialize_decomposition,
    validate_decomposition,
)
from agentos.planning.decomposition_governance import (
    gate_llm_governance_evidence,
)


def _templates() -> dict[str, SubAgentTemplate]:
    template = SubAgentTemplate(
        template_id="researcher",
        name="Researcher",
        role="Collect evidence.",
    )
    return {template.template_id: template}


def test_validation_and_gate_do_not_consume_materialization_ids() -> None:
    calls: list[str] = []
    decomposition = PlanDecomposition(
        objective="Review planner boundaries.",
        steps=(
            PlanStepSpec(
                instruction="Collect evidence.",
                template_id="researcher",
            ),
            PlanStepSpec(instruction="Write the review."),
        ),
    )

    validation = validate_decomposition(decomposition, templates=_templates())
    gate = gate_decomposition_proposal(
        {
            "objective": decomposition.objective,
            "steps": [
                {"instruction": "Collect evidence.", "template_id": "researcher"},
                {"instruction": "Write the review."},
            ],
        },
        templates=_templates(),
    )

    assert validation.ok is True
    assert gate.accepted is True
    assert calls == []

    def next_id(prefix: str) -> str:
        calls.append(prefix)
        return f"{prefix}_{len(calls)}"

    objective, steps = materialize_decomposition(
        decomposition,
        templates=_templates(),
        id_factory=next_id,
    )

    assert objective == "Review planner boundaries."
    assert [step.step_id for step in steps] == ["step_1", "step_2"]
    assert calls == ["step", "step"]


def test_decomposition_gate_rejects_malformed_and_disallowed_proposals() -> None:
    malformed = gate_decomposition_proposal(
        {"objective": "Invalid.", "steps": ["not an object"]},
        templates=_templates(),
    )
    restricted = gate_decomposition_proposal(
        {
            "objective": "Restricted.",
            "steps": [
                {
                    "step_id": "collect",
                    "instruction": "Collect evidence.",
                    "template_id": "researcher",
                },
                {"step_id": "write", "instruction": "Write the review."},
            ],
        },
        templates=_templates(),
        policy=PlanDecompositionGatePolicy(
            max_steps=1,
            require_template=True,
            allowed_template_ids=("reviewer",),
        ),
    )

    assert malformed.accepted is False
    assert malformed.errors == ("step 1 must be an object",)
    assert restricted.accepted is False
    assert restricted.missing_templates == ("write",)
    assert restricted.disallowed_templates == ("researcher",)


def test_materialization_rejects_invalid_dependency_graph() -> None:
    decomposition = PlanDecomposition(
        objective="Invalid dependency graph.",
        steps=(
            PlanStepSpec(
                step_id="review",
                instruction="Review evidence.",
                depends_on=("missing",),
            ),
        ),
    )

    with pytest.raises(ValueError, match="unknown dependency for review: missing"):
        materialize_decomposition(
            decomposition,
            templates={},
            id_factory=lambda prefix: f"{prefix}_1",
        )


def test_materialization_preserves_unknown_template_error_chain() -> None:
    decomposition = PlanDecomposition(
        objective="Unknown template.",
        steps=(
            PlanStepSpec(
                instruction="Collect evidence.",
                template_id="missing",
            ),
        ),
    )

    with pytest.raises(KeyError) as caught:
        materialize_decomposition(
            decomposition,
            templates={},
            id_factory=lambda prefix: f"{prefix}_1",
        )

    assert caught.value.args == ("missing",)
    assert isinstance(caught.value.__cause__, KeyError)


def test_llm_governance_gate_accepts_complete_and_rejects_failed_evidence() -> None:
    accepted = gate_llm_governance_evidence(
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_1",
            objective="Review planner boundaries.",
            prompt_ref="prompt://1",
            model_ref="model://1",
            approval_ref="approval://1",
            approved=True,
            evaluation_ref="evaluation://1",
            evaluation_passed=True,
            validation_ref="validation://1",
            validation_passed=True,
        ),
    )
    rejected = gate_llm_governance_evidence(
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_2",
            objective="Review planner boundaries.",
            prompt_ref="prompt://2",
            model_ref="model://2",
            approval_ref="approval://2",
            approved=True,
            evaluation_ref="evaluation://2",
            evaluation_passed=False,
            validation_ref="validation://2",
            validation_passed=True,
        ),
    )

    assert accepted.accepted is True
    assert accepted.block_plan_creation is False
    assert rejected.accepted is False
    assert rejected.block_plan_creation is True
    assert rejected.failed_evidence == ("evaluation_evidence",)
