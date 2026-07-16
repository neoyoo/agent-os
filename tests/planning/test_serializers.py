from agentos.planning.models import (
    EVIDENCE_KINDS,
    PLAN_ASSIGNMENT_DISPATCH_STATUSES,
    PLAN_STATUSES,
    PLAN_STEP_RETRY_STATUSES,
    PLAN_STEP_STATUSES,
    EvidenceHandle,
    PlanAssignment,
    PlanState,
    PlanStep,
)
from agentos.planning.serializers import (
    evidence_handle_from_dict,
    evidence_handle_to_dict,
    plan_assignment_from_dict,
    plan_assignment_to_dict,
    plan_state_from_dict,
    plan_state_to_dict,
    plan_step_from_dict,
    plan_step_to_dict,
)
from agentos.workspace import WORKSPACE_SCOPES, WorkspaceHandle


def test_serializers_accept_every_domain_owned_literal_value() -> None:
    for status in PLAN_STATUSES:
        plan = PlanState("plan_1", "Test serializers.", "owner", status=status)
        assert plan_state_from_dict(plan_state_to_dict(plan)) == plan

    for status in PLAN_STEP_STATUSES:
        step = PlanStep("step_1", "Test serializers.", status=status)
        assert plan_step_from_dict(plan_step_to_dict(step)) == step

    for retry_status in PLAN_STEP_RETRY_STATUSES:
        step = PlanStep(
            "step_1",
            "Test serializers.",
            retry_status=retry_status,
        )
        assert plan_step_from_dict(plan_step_to_dict(step)) == step

    for dispatch_status in PLAN_ASSIGNMENT_DISPATCH_STATUSES:
        assignment = PlanAssignment(
            "plan_1",
            "step_1",
            "template_1",
            "task_1",
            "agent_1",
            1.0,
            dispatch_status=dispatch_status,
        )
        assert plan_assignment_from_dict(plan_assignment_to_dict(assignment)) == assignment

    for kind in EVIDENCE_KINDS:
        evidence = EvidenceHandle("evidence_1", kind, "Test serializers.")
        assert evidence_handle_from_dict(evidence_handle_to_dict(evidence)) == evidence

    for scope in WORKSPACE_SCOPES:
        plan = PlanState(
            "plan_1",
            "Test serializers.",
            "owner",
            workspace=WorkspaceHandle("workspace_1", scope),
        )
        assert plan_state_from_dict(plan_state_to_dict(plan)) == plan
