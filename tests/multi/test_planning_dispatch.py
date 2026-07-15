import pytest

from agentos.multi.planning_dispatch import AgentCoordinatorPlanStepDispatcher
from agentos.multi.types import TaskAlreadySubmittedError
from agentos.planning import (
    PlanAssignment,
    PlanDispatchAlreadySubmittedError,
    PlanState,
    PlanStep,
    SubAgentTemplate,
)


class RecordingCoordinator:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[dict[str, object]] = []
        self.spawn_error: Exception | None = None

    def spawn(self, **kwargs: object) -> object:
        self.spawn_calls.append(kwargs)
        if self.spawn_error is not None:
            raise self.spawn_error
        return object()

    def dispatch(self, **kwargs: object) -> object:
        self.dispatch_calls.append(kwargs)
        return object()


def _plan_and_assignment() -> tuple[PlanState, PlanStep, PlanAssignment]:
    step = PlanStep(
        step_id="step_1",
        instruction="Collect evidence.",
    )
    assignment = PlanAssignment(
        plan_id="plan_1",
        step_id=step.step_id,
        template_id="researcher",
        task_id="task_1",
        target_agent_id="subagent_1",
        created_at=1.0,
    )
    plan = PlanState(
        plan_id="plan_1",
        objective="Review planner dispatch.",
        owner_agent_id="leader",
        steps=(step,),
        assignments=(assignment,),
    )
    return plan, step, assignment


def test_planning_dispatch_adapter_maps_spawn_without_leaking_handle() -> None:
    coordinator = RecordingCoordinator()
    dispatcher = AgentCoordinatorPlanStepDispatcher(coordinator)  # type: ignore[arg-type]
    plan, step, assignment = _plan_and_assignment()
    template = SubAgentTemplate(
        template_id="researcher",
        name="Researcher",
        role="Collect evidence.",
        allowed_tool_names=("search",),
        context_seed=("Tenant: acme", "Use primary sources."),
        timeout_seconds=45,
    )

    result = dispatcher.submit(
        plan=plan,
        step=step,
        assignment=assignment,
        template=template,
    )

    assert result is None
    assert coordinator.spawn_calls == [
        {
            "instruction": "Tenant: acme\nUse primary sources.\n\nTask: Collect evidence.",
            "allowed_tool_names": ("search",),
            "parent_agent_id": "leader",
            "timeout_seconds": 45,
            "task_id": "task_1",
            "child_agent_id": "subagent_1",
        },
    ]
    assert coordinator.dispatch_calls == []


def test_planning_dispatch_adapter_maps_persistent_dispatch_with_fallback() -> None:
    coordinator = RecordingCoordinator()
    dispatcher = AgentCoordinatorPlanStepDispatcher(coordinator)  # type: ignore[arg-type]
    plan, step, assignment = _plan_and_assignment()
    template = SubAgentTemplate(
        template_id="researcher",
        name="Researcher",
        role="Collect evidence.",
        capabilities=("research",),
        allowed_tool_names=("search",),
        target_agent_id="expert",
        timeout_seconds=90,
    )

    result = dispatcher.submit(
        plan=plan,
        step=step,
        assignment=assignment,
        template=template,
    )

    assert result is None
    assert coordinator.spawn_calls == []
    assert coordinator.dispatch_calls == [
        {
            "instruction": "Collect evidence.",
            "required_capabilities": ("research",),
            "parent_agent_id": "leader",
            "target_agent_id": "expert",
            "allowed_tool_names": ("search",),
            "timeout_seconds": 90,
            "task_id": "task_1",
        },
    ]


def test_planning_dispatch_adapter_translates_duplicate_task_error() -> None:
    coordinator = RecordingCoordinator()
    coordinator.spawn_error = TaskAlreadySubmittedError("task_1")
    dispatcher = AgentCoordinatorPlanStepDispatcher(coordinator)  # type: ignore[arg-type]
    plan, step, assignment = _plan_and_assignment()
    template = SubAgentTemplate(
        template_id="researcher",
        name="Researcher",
        role="Collect evidence.",
    )

    with pytest.raises(PlanDispatchAlreadySubmittedError, match="task_1") as caught:
        dispatcher.submit(
            plan=plan,
            step=step,
            assignment=assignment,
            template=template,
        )

    assert isinstance(caught.value.__cause__, TaskAlreadySubmittedError)
