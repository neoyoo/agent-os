from __future__ import annotations

import pytest

from agentos.planning import (
    PlannerDecompositionPolicyDeploymentProfile,
    PlannerLlmDecompositionGovernanceProfile,
    PlannerOrchestrationDeploymentProfile,
)


def test_planner_orchestration_profile_reports_missing_components() -> None:
    profile = PlannerOrchestrationDeploymentProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "PlannerOrchestrationDeploymentProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "decomposition_policy",
        "dag_scheduler",
        "worker_dispatch_loop",
        "compensation_policy",
        "plan_store",
        "worker_supervision",
    }
    assert "PlannerRuntime" in metadata["sdk_owned"]
    assert "production DAG scheduler" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_orchestration_profile_marks_ready_when_components_are_configured() -> None:
    profile = PlannerOrchestrationDeploymentProfile(
        configured_components=(
            "decomposition_policy",
            "dag_scheduler",
            "worker_dispatch_loop",
            "compensation_policy",
            "plan_store",
            "worker_supervision",
        ),
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_planner_orchestration_profile_rejects_empty_names() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        PlannerOrchestrationDeploymentProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerOrchestrationDeploymentProfile(configured_components=("",))


def test_planner_decomposition_policy_profile_reports_missing_components() -> None:
    profile = PlannerDecompositionPolicyDeploymentProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "PlannerDecompositionPolicyDeploymentProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "prompt_policy",
        "output_schema",
        "validation_gate",
        "template_mapping_policy",
        "approval_policy",
        "model_routing_policy",
        "evaluation_policy",
        "trace_logging",
        "rollback_policy",
    }
    assert "PlanDecompositionValidationReport" in metadata["sdk_owned"]
    assert "LLM decomposition prompt/policy" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_decomposition_policy_profile_marks_ready_when_components_are_configured() -> None:
    profile = PlannerDecompositionPolicyDeploymentProfile(
        configured_components=(
            "prompt_policy",
            "output_schema",
            "validation_gate",
            "template_mapping_policy",
            "approval_policy",
            "model_routing_policy",
            "evaluation_policy",
            "trace_logging",
            "rollback_policy",
        ),
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_planner_decomposition_policy_profile_rejects_empty_names() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        PlannerDecompositionPolicyDeploymentProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerDecompositionPolicyDeploymentProfile(configured_components=("",))


def test_planner_llm_governance_profile_reports_missing_references() -> None:
    profile = PlannerLlmDecompositionGovernanceProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "PlannerLlmDecompositionGovernanceProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert metadata["component_refs"] == {}
    assert metadata["evidence_refs"] == {}
    assert set(metadata["missing_components"]) == {
        "prompt_policy",
        "model_routing_policy",
        "approval_policy",
        "evaluation_policy",
        "trace_logging",
        "rollback_policy",
        "output_schema",
        "validation_gate",
        "template_mapping_policy",
        "budget_policy",
        "live_backend_verification",
    }
    assert "PlannerRuntime.gate_decomposition_proposal" in metadata["sdk_owned"]
    assert "prompt text and prompt review workflow" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_llm_governance_profile_marks_ready_with_policy_refs() -> None:
    refs = {
        "prompt_policy": "policy://planner/prompts/v3",
        "model_routing_policy": "router://planner/decomposition/v2",
        "approval_policy": "approval://planner/decomposition/human-gate",
        "evaluation_policy": "eval://planner/decomposition/regression-suite",
        "trace_logging": "trace://planner/decomposition",
        "rollback_policy": "release://planner/decomposition/rollback",
        "output_schema": "schema://planner/decomposition/v1",
        "validation_gate": "gate://planner/decomposition/pre-create",
        "template_mapping_policy": "policy://planner/templates/v4",
        "budget_policy": "budget://planner/decomposition/default",
        "live_backend_verification": "probe://planner/decomposition/live",
    }
    profile = PlannerLlmDecompositionGovernanceProfile(
        component_refs=refs,
        evidence_refs={
            "prompt_policy": (
                "docs://reviews/planner-prompt-v3",
                "ci://planner-evals/2026-06-16",
            ),
            "evaluation_policy": ("artifact://eval-report/planner-v3",),
        },
        metadata={
            "release": "planner-decomposition-v3",
            "schema_version": 1,
            "approval_required": True,
        },
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert profile.configured_component_names() == tuple(refs)
    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert metadata["component_refs"] == refs
    assert metadata["evidence_refs"]["prompt_policy"] == (
        "docs://reviews/planner-prompt-v3",
        "ci://planner-evals/2026-06-16",
    )
    assert metadata["metadata"] == {
        "release": "planner-decomposition-v3",
        "schema_version": 1,
        "approval_required": True,
    }
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_planner_llm_governance_profile_rejects_invalid_refs() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        PlannerLlmDecompositionGovernanceProfile(probe_name=" ")
    with pytest.raises(ValueError, match="component_refs"):
        PlannerLlmDecompositionGovernanceProfile(
            component_refs={"prompt_policy": " "},
        )
    with pytest.raises(ValueError, match="component_refs"):
        PlannerLlmDecompositionGovernanceProfile(
            component_refs={" ": "policy://planner"},
        )
    with pytest.raises(ValueError, match="evidence_refs"):
        PlannerLlmDecompositionGovernanceProfile(
            evidence_refs={"prompt_policy": ("",)},
        )
    with pytest.raises(ValueError, match="metadata"):
        PlannerLlmDecompositionGovernanceProfile(
            metadata={"not_json": object()},
        )
