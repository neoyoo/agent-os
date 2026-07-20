from __future__ import annotations

from agentos.multi.coordinator import AgentCoordinator
from agentos.multi.types import TaskAlreadySubmittedError
from agentos.planning import (
    PlanAssignment,
    PlanDispatchAlreadySubmittedError,
    PlanState,
    PlanStep,
    SubAgentTemplate,
)


class AgentCoordinatorPlanStepDispatcher:
    """通过 AgentCoordinator 提交 Plan Step。"""

    def __init__(self, coordinator: AgentCoordinator) -> None:
        self._coordinator = coordinator

    async def submit(
        self,
        *,
        plan: PlanState,
        step: PlanStep,
        assignment: PlanAssignment,
        template: SubAgentTemplate,
    ) -> None:
        instruction = self._instruction(step, template)
        try:
            if template.target_agent_id is None:
                self._coordinator.spawn(
                    instruction=instruction,
                    allowed_tool_names=template.allowed_tool_names,
                    parent_agent_id=plan.owner_agent_id,
                    timeout_seconds=template.timeout_seconds,
                    task_id=assignment.task_id,
                    child_agent_id=assignment.target_agent_id,
                )
                return
            self._coordinator.dispatch(
                instruction=instruction,
                required_capabilities=(
                    step.required_capabilities or template.capabilities
                ),
                parent_agent_id=plan.owner_agent_id,
                target_agent_id=template.target_agent_id,
                allowed_tool_names=template.allowed_tool_names,
                timeout_seconds=template.timeout_seconds,
                task_id=assignment.task_id,
            )
        except TaskAlreadySubmittedError as error:
            raise PlanDispatchAlreadySubmittedError(str(error)) from error

    def _instruction(self, step: PlanStep, template: SubAgentTemplate) -> str:
        context = "\n".join(template.context_seed)
        if not context:
            return step.instruction
        return f"{context}\n\nTask: {step.instruction}"
