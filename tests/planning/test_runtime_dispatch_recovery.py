from __future__ import annotations

from tests.planning._async import async_test


from agentos.planning import (
    InMemoryPlanStore,
    PlanAssignment,
    PlanDispatchAlreadySubmittedError,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from tests.planning._runtime_fixtures import (
    FakeCoordinator,
)


@async_test
async def test_planner_runtime_recovers_pending_assignment_after_restart() -> None:
    store = InMemoryPlanStore()
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover a saved but unsubmitted planner assignment.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Dispatch after restart.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_1",
                    assigned_agent_id="expert_reviewer",
                    required_capabilities=("architecture-review",),
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_1",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
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
        clock=lambda: 20.0,
    )

    report = await runtime.recover_pending_dispatches("plan_1")

    persisted = await runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_1"]
    assert report.skipped == ()
    assert coordinator.dispatch_calls == [
        {
            "instruction": "Dispatch after restart.",
            "required_capabilities": ("architecture-review",),
            "parent_agent_id": "leader",
            "target_agent_id": "expert_reviewer",
            "allowed_tool_names": (),
            "timeout_seconds": 300,
            "task_id": "task_1",
        },
    ]
    assert persisted.steps[0].status == "assigned"
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].submitted_at == 20.0
    assert persisted.assignments[0].dispatch_error is None


@async_test
async def test_planner_runtime_treats_duplicate_task_recovery_as_submitted() -> None:
    class DuplicateTaskCoordinator(FakeCoordinator):
        def dispatch(self, **kwargs: object) -> None:
            self.dispatch_calls.append(kwargs)
            raise PlanDispatchAlreadySubmittedError(str(kwargs["task_id"]))

    store = InMemoryPlanStore()
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover a task that the coordinator already accepted.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover duplicate task.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    coordinator = DuplicateTaskCoordinator()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 20.0,
    )

    report = await runtime.recover_pending_dispatches("plan_1")

    persisted = await runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_1"]
    assert report.skipped == ()
    assert coordinator.dispatch_calls[0]["task_id"] == "task_existing"
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[0].error is None
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].dispatch_error is None


@async_test
async def test_planner_runtime_treats_spawn_duplicate_task_recovery_as_submitted() -> (
    None
):
    class DuplicateSpawnCoordinator(FakeCoordinator):
        def spawn(self, **kwargs: object) -> None:
            self.spawn_calls.append(kwargs)
            raise PlanDispatchAlreadySubmittedError(str(kwargs["task_id"]))

    store = InMemoryPlanStore()
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover a spawned task accepted before restart.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover duplicate spawned task.",
                    status="assigned",
                    template_id="reviewer",
                    task_id="task_existing",
                    assigned_agent_id="subagent_existing",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="reviewer",
                    task_id="task_existing",
                    target_agent_id="subagent_existing",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    coordinator = DuplicateSpawnCoordinator()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review architecture.",
            ),
        ),
        clock=lambda: 20.0,
    )

    report = await runtime.recover_pending_dispatches("plan_1")

    persisted = await runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_1"]
    assert report.skipped == ()
    assert coordinator.spawn_calls[0]["task_id"] == "task_existing"
    assert coordinator.spawn_calls[0]["child_agent_id"] == "subagent_existing"
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[0].error is None
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].dispatch_error is None


@async_test
async def test_planner_runtime_dispatch_ready_steps_recovers_before_new_assignments() -> (
    None
):
    store = InMemoryPlanStore()
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover existing assignment before creating more work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover first.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Dispatch second.",
                    status="pending",
                    template_id="expert-reviewer",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 20.0,
        id_factory=lambda prefix: f"{prefix}_new",
    )

    report = await runtime.dispatch_ready_steps("plan_1")

    persisted = await runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == [
        "step_1",
        "step_2",
    ]
    assert [assignment.task_id for assignment in report.assigned] == [
        "task_existing",
        "task_new",
    ]
    assert [assignment.dispatch_status for assignment in persisted.assignments] == [
        "submitted",
        "submitted",
    ]
    assert [call["task_id"] for call in coordinator.dispatch_calls] == [
        "task_existing",
        "task_new",
    ]
