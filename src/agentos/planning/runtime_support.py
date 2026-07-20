from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from uuid import uuid4

from agentos.planning.errors import PlanStepNotFoundError
from agentos.planning.models import (
    PLAN_STATUSES,
    PlanAssignment,
    PlanState,
    PlanStatus,
    PlanStep,
    SubAgentTemplate,
)


def require_template(
    templates: Mapping[str, SubAgentTemplate],
    template_id: str,
) -> SubAgentTemplate:
    try:
        return templates[template_id]
    except KeyError as error:
        raise KeyError(template_id) from error


def require_step(plan: PlanState, step_id: str) -> PlanStep:
    for step in plan.steps:
        if step.step_id == step_id:
            return step
    raise PlanStepNotFoundError(step_id)


def validate_plan_statuses(
    statuses: tuple[PlanStatus, ...],
) -> tuple[PlanStatus, ...]:
    if not statuses:
        raise ValueError("statuses must not be empty")
    if any(status not in PLAN_STATUSES for status in statuses):
        raise ValueError("statuses contains an unsupported plan status")
    return tuple(dict.fromkeys(statuses))


def replace_plan_step(
    plan: PlanState,
    step: PlanStep,
    *,
    assignments: tuple[PlanAssignment, ...] | None,
    updated_at: float,
) -> PlanState:
    return replace(
        plan,
        status="running" if plan.status == "draft" else plan.status,
        steps=tuple(
            step if item.step_id == step.step_id else item for item in plan.steps
        ),
        assignments=plan.assignments if assignments is None else assignments,
        updated_at=updated_at,
    )


def default_runtime_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
