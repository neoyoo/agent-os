from __future__ import annotations


import pytest

from agentos.planning import (
    InMemoryPlanStore,
    PlanConflictError,
    PlanDispatchAlreadySubmittedError,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from tests.planning._runtime_fixtures import (
    ConflictOncePlanStore,
    FakeCoordinator,
    RejectingPlanStore,
)


def test_planner_runtime_assigns_step_to_spawn_template() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
                allowed_tool_names=("read_file",),
                timeout_seconds=30,
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    plan = runtime.add_step(
        plan.plan_id,
        instruction="Review planner module.",
        template_id="reviewer",
    )

    assigned = runtime.assign_step(plan.plan_id, "step_1", template_id="reviewer")

    assert assigned.steps[0].status == "assigned"
    assert assigned.steps[0].task_id == "task_1"
    assert assigned.steps[0].assigned_agent_id == "subagent_1"
    assert assigned.assignments[0].task_id == "task_1"
    assert assigned.assignments[0].dispatch_status == "submitted"
    assert assigned.assignments[0].submitted_at == 10.0
    assert assigned.assignments[0].dispatch_error is None
    assert coordinator.spawn_calls[0]["task_id"] == "task_1"
    assert coordinator.spawn_calls[0]["allowed_tool_names"] == ("read_file",)
    assert coordinator.spawn_calls[0]["timeout_seconds"] == 30


def test_planner_runtime_retries_submitted_marker_after_revision_conflict() -> None:
    store = ConflictOncePlanStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 20.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(
        plan.plan_id,
        instruction="Review planner module.",
        template_id="reviewer",
    )

    assigned = runtime.assign_step(plan.plan_id, "step_1", template_id="reviewer")

    persisted = runtime.get_plan(plan.plan_id)
    assert len(coordinator.spawn_calls) == 1
    assert assigned.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].submitted_at == 20.0
    assert store.conflict_next_submitted_save is False


def test_planner_runtime_fresh_assignment_duplicate_task_is_not_submitted() -> None:
    class DuplicateTaskCoordinator(FakeCoordinator):
        def spawn(self, **kwargs: object) -> None:
            self.spawn_calls.append(kwargs)
            raise PlanDispatchAlreadySubmittedError(str(kwargs["task_id"]))

    store = InMemoryPlanStore()
    coordinator = DuplicateTaskCoordinator()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 20.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(
        plan.plan_id,
        instruction="Review planner module.",
        template_id="reviewer",
    )

    with pytest.raises(PlanDispatchAlreadySubmittedError, match="task_1"):
        runtime.assign_step(plan.plan_id, "step_1", template_id="reviewer")

    persisted = runtime.get_plan(plan.plan_id)
    assert persisted.steps[0].status == "failed"
    assert persisted.assignments[0].dispatch_status == "failed"
    assert persisted.assignments[0].dispatch_error == "task_1"


def test_planner_runtime_assigns_step_to_dispatch_template() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                capabilities=("architecture-review",),
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(
        plan.plan_id,
        instruction="Review architecture.",
        required_capabilities=("architecture-review",),
        template_id="expert-reviewer",
    )

    assigned = runtime.assign_step(
        plan.plan_id,
        "step_1",
        template_id="expert-reviewer",
    )

    assert assigned.steps[0].task_id == "task_1"
    assert assigned.steps[0].assigned_agent_id == "expert_reviewer"
    assert coordinator.dispatch_calls[0]["task_id"] == "task_1"
    assert coordinator.dispatch_calls[0]["target_agent_id"] == "expert_reviewer"
    assert coordinator.dispatch_calls[0]["required_capabilities"] == (
        "architecture-review",
    )


def test_planner_runtime_does_not_dispatch_when_assignment_save_fails() -> None:
    coordinator = FakeCoordinator()
    store = RejectingPlanStore()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Guard planner dispatch side effects.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not dispatch if save fails.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    with pytest.raises(PlanConflictError, match="plan changed before saving"):
        runtime.assign_step("plan_1", "step_1", template_id="reviewer")

    assert coordinator.spawn_calls == []
    assert coordinator.dispatch_calls == []
    assert store.get_plan("plan_1").steps[0].status == "pending"  # type: ignore[union-attr]
