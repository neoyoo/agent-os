from __future__ import annotations

from dataclasses import replace

from agentos.planning import (
    InMemoryPlanStore,
    PlanAssignment,
    PlanClaimRecord,
    PlanState,
    PlanStep,
    SubAgentTemplate,
)


class ManualClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeCoordinator:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[dict[str, object]] = []

    def submit(
        self,
        *,
        plan: PlanState,
        step: PlanStep,
        assignment: PlanAssignment,
        template: SubAgentTemplate,
    ) -> None:
        instruction = step.instruction
        if template.context_seed:
            context = "\n".join(template.context_seed)
            instruction = f"{context}\n\nTask: {instruction}"
        if template.target_agent_id is None:
            self.spawn(
                instruction=instruction,
                allowed_tool_names=template.allowed_tool_names,
                parent_agent_id=plan.owner_agent_id,
                timeout_seconds=template.timeout_seconds,
                task_id=assignment.task_id,
                child_agent_id=assignment.target_agent_id,
            )
            return
        self.dispatch(
            instruction=instruction,
            required_capabilities=(step.required_capabilities or template.capabilities),
            parent_agent_id=plan.owner_agent_id,
            target_agent_id=template.target_agent_id,
            allowed_tool_names=template.allowed_tool_names,
            timeout_seconds=template.timeout_seconds,
            task_id=assignment.task_id,
        )

    def spawn(self, **kwargs: object) -> None:
        self.spawn_calls.append(kwargs)

    def dispatch(self, **kwargs: object) -> None:
        self.dispatch_calls.append(kwargs)


class RejectingPlanStore(InMemoryPlanStore):
    def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        return False

    def save_plan_if_claimed(
        self,
        plan: PlanState,
        claim: PlanClaimRecord,
        *,
        expected_revision: int,
        now: float,
    ) -> bool:
        return False


class ConflictOncePlanStore(InMemoryPlanStore):
    def __init__(self) -> None:
        super().__init__()
        self.conflict_next_submitted_save = True

    def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        if self.conflict_next_submitted_save and any(
            assignment.dispatch_status == "submitted" for assignment in plan.assignments
        ):
            self.conflict_next_submitted_save = False
            current = self.get_plan(plan.plan_id)
            assert current is not None
            super().save_plan(replace(current, updated_at=10.5))
            return False
        return super().save_plan_if_unchanged(
            plan,
            expected_revision=expected_revision,
        )
