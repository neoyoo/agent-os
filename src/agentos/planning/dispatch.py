from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from agentos.planning.models import (
    PlanAssignment,
    PlanState,
    PlanStep,
    SubAgentTemplate,
)


PlanDispatchSkipReason = Literal[
    "missing-template",
    "unknown-template",
    "dispatch-failed",
]


class PlanStepDispatcher(Protocol):
    """Plan Step 到外部执行系统的提交边界。"""

    def submit(
        self,
        *,
        plan: PlanState,
        step: PlanStep,
        assignment: PlanAssignment,
        template: SubAgentTemplate,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PlanDispatchSkip:
    """一次批量派发中未提交的就绪 Step。"""

    plan_id: str
    step_id: str
    reason: PlanDispatchSkipReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PlanDispatchReport:
    """一次就绪 Step 批量派发的瞬时报告。"""

    plan_id: str
    assigned: tuple[PlanAssignment, ...] = ()
    skipped: tuple[PlanDispatchSkip, ...] = ()
