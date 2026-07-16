from __future__ import annotations

import json

from agentos.planning.dispatch import PlanDispatchReport
from agentos.planning.models import (
    EvidenceHandle,
    PlanAssignment,
    PlanState,
    PlanStep,
)
from agentos.planning.scheduling_reports import (
    PlanClaimedSchedulerTickReport,
    PlanSchedulerTickReport,
)


def json_result(value: object) -> str:
    """使用 Planner Tool 的稳定 JSON 编码输出结果。"""

    return json.dumps(value, sort_keys=True)


def plan_to_dict(plan: PlanState) -> dict[str, object]:
    return {
        "plan_id": plan.plan_id,
        "objective": plan.objective,
        "owner_agent_id": plan.owner_agent_id,
        "status": plan.status,
        "steps": [step_to_dict(step) for step in plan.steps],
        "evidence": [evidence_to_dict(evidence) for evidence in plan.evidence],
        "assignments": [
            assignment_to_dict(assignment) for assignment in plan.assignments
        ],
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def step_to_dict(step: PlanStep) -> dict[str, object]:
    return {
        "step_id": step.step_id,
        "instruction": step.instruction,
        "status": step.status,
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


def evidence_to_dict(evidence: EvidenceHandle) -> dict[str, object]:
    return {
        "evidence_id": evidence.evidence_id,
        "kind": evidence.kind,
        "summary": evidence.summary,
        "uri": evidence.uri,
        "producer_agent_id": evidence.producer_agent_id,
        "metadata": dict(evidence.metadata),
    }


def assignment_to_dict(assignment: PlanAssignment) -> dict[str, object]:
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


def dispatch_report_to_dict(report: PlanDispatchReport) -> dict[str, object]:
    return {
        "plan_id": report.plan_id,
        "assigned": [
            assignment_to_dict(assignment) for assignment in report.assigned
        ],
        "skipped": [
            {
                "plan_id": skip.plan_id,
                "step_id": skip.step_id,
                "reason": skip.reason,
                "detail": skip.detail,
            }
            for skip in report.skipped
        ],
    }


def scheduler_tick_report_to_dict(
    report: PlanSchedulerTickReport,
) -> dict[str, object]:
    return {
        "plan_id": report.plan_id,
        "retry_resets": [
            {
                "plan_id": reset.plan_id,
                "step_id": reset.step_id,
                "attempts": reset.attempts,
            }
            for reset in report.retry_resets
        ],
        "dispatch": dispatch_report_to_dict(report.dispatch),
    }


def claimed_scheduler_tick_report_to_dict(
    report: PlanClaimedSchedulerTickReport,
) -> dict[str, object]:
    return {
        "worker_id": report.worker_id,
        "claims": [claim.as_dict() for claim in report.claims],
        "tick_reports": [
            scheduler_tick_report_to_dict(tick_report)
            for tick_report in report.tick_reports
        ],
        "skipped": [skip.as_dict() for skip in report.skipped],
        "released_plan_ids": list(report.released_plan_ids),
    }
