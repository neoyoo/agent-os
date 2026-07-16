from __future__ import annotations

from dataclasses import dataclass

from agentos.context.models import ContextSlotProjection, ProjectionVariant
from agentos.context.xml import XmlElement
from agentos.planning.errors import PlanNotFoundError, PlanProjectionError
from agentos.planning.models import PlanState, PlanStep
from agentos.planning.store import PlanStore


_ACTIVE_STEP_STATUSES = frozenset(("pending", "assigned", "running"))
_BLOCKED_STEP_STATUSES = frozenset(("blocked", "failed"))
_STEP_STATUS_MAP = {
    "pending": "pending",
    "assigned": "in-progress",
    "running": "in-progress",
    "blocked": "blocked",
    "failed": "blocked",
    "completed": "completed",
    "cancelled": "cancelled",
}
_TERMINAL_PLAN_STATUSES = frozenset(("completed", "failed", "cancelled"))


@dataclass(frozen=True, slots=True)
class AuthorizedPlanSource:
    """按 Plan 和 Owner Scope 从权威 Store 读取投影真值。"""

    store: PlanStore

    def get_for_projection(self, plan_id: str, owner_agent_id: str) -> PlanState:
        """返回授权 Plan；不存在与 Owner 不匹配使用同一 not-found。"""

        plan = self.store.get_plan(plan_id)
        if plan is None or plan.owner_agent_id != owner_agent_id:
            raise PlanNotFoundError("plan not found")
        return plan


@dataclass(frozen=True, slots=True)
class BoundPlanProjectionProvider:
    """把无参 Context Projection Port 绑定到一个 Plan Scope。"""

    source: AuthorizedPlanSource
    plan_id: str
    owner_agent_id: str

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        """重新读取权威 Plan 并生成当前 active-plan 投影。"""

        plan = self.source.get_for_projection(self.plan_id, self.owner_agent_id)
        return _project_active_plan(plan)


def _project_active_plan(plan: PlanState) -> tuple[ContextSlotProjection, ...]:
    if plan.status in _TERMINAL_PLAN_STATUSES:
        return ()
    context_status = _context_plan_status(plan)
    completed_indexes = tuple(
        index for index, step in enumerate(plan.steps) if step.status == "completed"
    )
    variants = tuple(
        ProjectionVariant(
            element=_plan_element(
                plan,
                context_status,
                omitted_indexes=frozenset(completed_indexes[:omitted_count]),
            ),
            omitted_count=omitted_count,
        )
        for omitted_count in range(len(completed_indexes) + 1)
    )
    return (
        ContextSlotProjection(
            slot="active-plan",
            owner="PlannerRuntime",
            variants=variants,
        ),
    )


def _context_plan_status(plan: PlanState) -> str:
    if plan.status == "draft":
        return "pending"
    if plan.status != "running":
        raise PlanProjectionError("invalid active plan state")
    step_statuses = frozenset(step.status for step in plan.steps)
    if step_statuses & _ACTIVE_STEP_STATUSES:
        return "in-progress"
    if step_statuses & _BLOCKED_STEP_STATUSES:
        return "blocked"
    raise PlanProjectionError("invalid active plan state")


def _plan_element(
    plan: PlanState,
    status: str,
    *,
    omitted_indexes: frozenset[int],
) -> XmlElement:
    return XmlElement(
        "active-plan",
        (("status", status),),
        children=(
            XmlElement("goal", text=plan.objective),
            *(
                _step_element(step)
                for index, step in enumerate(plan.steps)
                if index not in omitted_indexes
            ),
        ),
    )


def _step_element(step: PlanStep) -> XmlElement:
    try:
        status = _STEP_STATUS_MAP[step.status]
    except KeyError:
        raise PlanProjectionError("invalid active plan state") from None
    return XmlElement(
        "step",
        (("handle", step.step_id), ("status", status)),
        text=step.instruction,
    )
