from __future__ import annotations

from typing import Any, cast

from agentos.planning.models import (
    EVIDENCE_KINDS,
    PLAN_ASSIGNMENT_DISPATCH_STATUSES,
    PLAN_STATUSES,
    PLAN_STEP_RETRY_STATUSES,
    PLAN_STEP_STATUSES,
    EvidenceHandle,
    EvidenceKind,
    PlanAssignment,
    PlanAssignmentDispatchStatus,
    PlanState,
    PlanStatus,
    PlanStep,
    PlanStepRetryStatus,
    PlanStepStatus,
)
from agentos.workspace import WorkspaceHandle, WorkspaceScope


JsonDict = dict[str, Any]


def plan_step_to_dict(step: PlanStep) -> JsonDict:
    """Serialize PlanStep."""

    return {
        "step_id": step.step_id,
        "instruction": step.instruction,
        "status": _plan_step_status(step.status),
        "required_capabilities": list(step.required_capabilities),
        "assigned_agent_id": step.assigned_agent_id,
        "template_id": step.template_id,
        "task_id": step.task_id,
        "depends_on": list(step.depends_on),
        "evidence_ids": list(step.evidence_ids),
        "error": step.error,
        "attempts": step.attempts,
        "last_failed_at": step.last_failed_at,
        "next_retry_at": step.next_retry_at,
        "retry_status": step.retry_status,
        "retry_exhausted_at": step.retry_exhausted_at,
    }


def plan_step_from_dict(data: JsonDict) -> PlanStep:
    """Deserialize PlanStep."""

    return PlanStep(
        step_id=str(data["step_id"]),
        instruction=str(data["instruction"]),
        status=_plan_step_status(data.get("status", "pending")),
        required_capabilities=tuple(
            str(item) for item in data.get("required_capabilities", [])
        ),
        assigned_agent_id=(
            None
            if data.get("assigned_agent_id") is None
            else str(data["assigned_agent_id"])
        ),
        template_id=None if data.get("template_id") is None else str(data["template_id"]),
        task_id=None if data.get("task_id") is None else str(data["task_id"]),
        depends_on=tuple(str(item) for item in data.get("depends_on", [])),
        evidence_ids=tuple(str(item) for item in data.get("evidence_ids", [])),
        error=None if data.get("error") is None else str(data["error"]),
        attempts=int(data.get("attempts", 0)),
        last_failed_at=(
            None
            if data.get("last_failed_at") is None
            else float(data["last_failed_at"])
        ),
        next_retry_at=(
            None
            if data.get("next_retry_at") is None
            else float(data["next_retry_at"])
        ),
        retry_status=(
            None
            if data.get("retry_status") is None
            else _plan_step_retry_status(data["retry_status"])
        ),
        retry_exhausted_at=(
            None
            if data.get("retry_exhausted_at") is None
            else float(data["retry_exhausted_at"])
        ),
    )


def evidence_handle_to_dict(evidence: EvidenceHandle) -> JsonDict:
    """Serialize EvidenceHandle."""

    return {
        "evidence_id": evidence.evidence_id,
        "kind": _evidence_kind(evidence.kind),
        "summary": evidence.summary,
        "uri": evidence.uri,
        "producer_agent_id": evidence.producer_agent_id,
        "metadata": {
            str(key): str(value)
            for key, value in dict(evidence.metadata).items()
        },
    }


def evidence_handle_from_dict(data: JsonDict) -> EvidenceHandle:
    """Deserialize EvidenceHandle."""

    return EvidenceHandle(
        evidence_id=str(data["evidence_id"]),
        kind=_evidence_kind(data["kind"]),
        summary=str(data["summary"]),
        uri=None if data.get("uri") is None else str(data["uri"]),
        producer_agent_id=(
            None
            if data.get("producer_agent_id") is None
            else str(data["producer_agent_id"])
        ),
        metadata={
            str(key): str(value)
            for key, value in dict(data.get("metadata", {})).items()
        },
    )


def plan_assignment_to_dict(assignment: PlanAssignment) -> JsonDict:
    """Serialize PlanAssignment."""

    return {
        "plan_id": assignment.plan_id,
        "step_id": assignment.step_id,
        "template_id": assignment.template_id,
        "task_id": assignment.task_id,
        "target_agent_id": assignment.target_agent_id,
        "created_at": assignment.created_at,
        "dispatch_status": assignment.dispatch_status,
        "submitted_at": assignment.submitted_at,
        "dispatch_error": assignment.dispatch_error,
    }


def plan_assignment_from_dict(data: JsonDict) -> PlanAssignment:
    """Deserialize PlanAssignment."""

    return PlanAssignment(
        plan_id=str(data["plan_id"]),
        step_id=str(data["step_id"]),
        template_id=str(data["template_id"]),
        task_id=str(data["task_id"]),
        target_agent_id=str(data["target_agent_id"]),
        created_at=float(data["created_at"]),
        dispatch_status=_plan_assignment_dispatch_status(
            data.get("dispatch_status", "pending"),
        ),
        submitted_at=(
            None
            if data.get("submitted_at") is None
            else float(data["submitted_at"])
        ),
        dispatch_error=(
            None
            if data.get("dispatch_error") is None
            else str(data["dispatch_error"])
        ),
    )


def plan_state_to_dict(plan: PlanState) -> JsonDict:
    """Serialize PlanState."""

    return {
        "plan_id": plan.plan_id,
        "objective": plan.objective,
        "owner_agent_id": plan.owner_agent_id,
        "status": _plan_status(plan.status),
        "steps": [plan_step_to_dict(step) for step in plan.steps],
        "evidence": [evidence_handle_to_dict(evidence) for evidence in plan.evidence],
        "assignments": [
            plan_assignment_to_dict(assignment)
            for assignment in plan.assignments
        ],
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "workspace": (
            None if plan.workspace is None else _workspace_handle_to_dict(plan.workspace)
        ),
    }


def plan_state_from_dict(data: JsonDict) -> PlanState:
    """Deserialize PlanState."""

    workspace = data.get("workspace")
    return PlanState(
        plan_id=str(data["plan_id"]),
        objective=str(data["objective"]),
        owner_agent_id=str(data["owner_agent_id"]),
        status=_plan_status(data.get("status", "draft")),
        steps=tuple(
            plan_step_from_dict(cast(JsonDict, step))
            for step in data.get("steps", [])
        ),
        evidence=tuple(
            evidence_handle_from_dict(cast(JsonDict, evidence))
            for evidence in data.get("evidence", [])
        ),
        assignments=tuple(
            plan_assignment_from_dict(cast(JsonDict, assignment))
            for assignment in data.get("assignments", [])
        ),
        created_at=float(data.get("created_at", 0)),
        updated_at=float(data.get("updated_at", 0)),
        workspace=(
            None
            if workspace is None
            else _workspace_handle_from_dict(cast(JsonDict, workspace))
        ),
    )


def _workspace_handle_to_dict(handle: WorkspaceHandle) -> JsonDict:
    return {
        "workspace_id": handle.workspace_id,
        "scope": handle.scope,
        "root": handle.root,
        "parent_workspace_id": handle.parent_workspace_id,
        "metadata": {
            str(key): str(value)
            for key, value in dict(handle.metadata).items()
        },
    }


def _workspace_handle_from_dict(data: JsonDict) -> WorkspaceHandle:
    return WorkspaceHandle(
        workspace_id=str(data["workspace_id"]),
        scope=cast(WorkspaceScope, str(data["scope"])),
        root=_optional_string(data.get("root")),
        parent_workspace_id=_optional_string(data.get("parent_workspace_id")),
        metadata={
            str(key): str(value)
            for key, value in dict(data.get("metadata", {})).items()
        },
    )


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _plan_status(value: object) -> PlanStatus:
    status = str(value)
    if status not in PLAN_STATUSES:
        raise ValueError(f"invalid plan status: {status}")
    return cast(PlanStatus, status)


def _plan_step_status(value: object) -> PlanStepStatus:
    status = str(value)
    if status not in PLAN_STEP_STATUSES:
        raise ValueError(f"invalid plan step status: {status}")
    return cast(PlanStepStatus, status)


def _plan_step_retry_status(value: object) -> PlanStepRetryStatus:
    status = str(value)
    if status not in PLAN_STEP_RETRY_STATUSES:
        raise ValueError(f"invalid plan step retry status: {status}")
    return cast(PlanStepRetryStatus, status)


def _plan_assignment_dispatch_status(value: object) -> PlanAssignmentDispatchStatus:
    status = str(value)
    if status not in PLAN_ASSIGNMENT_DISPATCH_STATUSES:
        raise ValueError(f"invalid plan assignment dispatch status: {status}")
    return cast(PlanAssignmentDispatchStatus, status)


def _evidence_kind(value: object) -> EvidenceKind:
    kind = str(value)
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"invalid evidence kind: {kind}")
    return cast(EvidenceKind, kind)


__all__ = [
    "evidence_handle_from_dict",
    "evidence_handle_to_dict",
    "plan_assignment_from_dict",
    "plan_assignment_to_dict",
    "plan_state_from_dict",
    "plan_state_to_dict",
    "plan_step_from_dict",
    "plan_step_to_dict",
]
