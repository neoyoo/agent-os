from __future__ import annotations

from dataclasses import dataclass

from agentos.planning import (
    InMemoryPlanStore,
    PlanAssignment,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)


@dataclass(frozen=True, slots=True)
class PlanStepSubmitCall:
    plan: PlanState
    step: PlanStep
    assignment: PlanAssignment
    template: SubAgentTemplate


class FakePlanStepDispatcher:
    def __init__(self) -> None:
        self.submit_calls: list[PlanStepSubmitCall] = []

    def submit(
        self,
        *,
        plan: PlanState,
        step: PlanStep,
        assignment: PlanAssignment,
        template: SubAgentTemplate,
    ) -> None:
        self.submit_calls.append(
            PlanStepSubmitCall(
                plan=plan,
                step=step,
                assignment=assignment,
                template=template,
            ),
        )


class StatusRuntimeSpy(PlannerRuntime):
    def __init__(self) -> None:
        super().__init__(store=InMemoryPlanStore(), clock=lambda: 10.0)
        self.get_plan_calls: list[tuple[str, str | None]] = []
        self.list_plan_calls: list[str | None] = []

    def get_plan(self, plan_id: str, *, owner_agent_id: str | None = None):
        self.get_plan_calls.append((plan_id, owner_agent_id))
        return super().get_plan(plan_id, owner_agent_id=owner_agent_id)

    def list_plans(self, owner_agent_id: str | None = None):
        self.list_plan_calls.append(owner_agent_id)
        return super().list_plans(owner_agent_id)


class ManualClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value
