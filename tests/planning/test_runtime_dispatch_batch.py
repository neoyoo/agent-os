from __future__ import annotations

from dataclasses import replace


from agentos.planning import (
    InMemoryPlanStore,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from tests.planning._runtime_fixtures import (
    FakeCoordinator,
)


def test_planner_runtime_dispatches_ready_steps_with_limit() -> None:
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
            ),
        ),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Dispatch ready planner steps.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Collect evidence.",
                    status="completed",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Review evidence.",
                    status="pending",
                    depends_on=("step_1",),
                    template_id="reviewer",
                    required_capabilities=("review",),
                ),
                PlanStep(
                    step_id="step_3",
                    instruction="Review independent module.",
                    status="pending",
                    template_id="reviewer",
                    required_capabilities=("review",),
                ),
                PlanStep(
                    step_id="step_4",
                    instruction="Wait for step three.",
                    status="pending",
                    depends_on=("step_3",),
                    template_id="reviewer",
                ),
                PlanStep(
                    step_id="step_5",
                    instruction="Already assigned.",
                    status="assigned",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.dispatch_ready_steps("plan_1", limit=1)

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_2"]
    assert report.skipped == ()
    assert persisted.steps[1].status == "assigned"
    assert persisted.steps[1].task_id is not None
    assert persisted.steps[2].status == "pending"
    assert persisted.steps[3].status == "pending"
    assert persisted.steps[4].status == "assigned"
    assert len(coordinator.spawn_calls) == 1
    assert coordinator.spawn_calls[0]["task_id"] == persisted.steps[1].task_id


def test_planner_runtime_dispatch_ready_steps_reports_template_skips() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Dispatch ready planner steps.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Missing template.",
                    status="pending",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Unknown template.",
                    status="pending",
                    template_id="missing",
                ),
                PlanStep(
                    step_id="step_3",
                    instruction="Known default template.",
                    status="pending",
                ),
            ),
        ),
    )

    report = runtime.dispatch_ready_steps(
        "plan_1",
        default_template_id="reviewer",
    )

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == [
        "step_1",
        "step_3",
    ]
    assert [(skip.step_id, skip.reason) for skip in report.skipped] == [
        ("step_2", "unknown-template"),
    ]
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[1].status == "pending"
    assert persisted.steps[2].status == "assigned"


class FailingCoordinator(FakeCoordinator):
    def dispatch(self, **kwargs: object) -> None:
        self.dispatch_calls.append(kwargs)
        raise RuntimeError("expert unavailable")


def test_planner_runtime_dispatch_ready_steps_reports_coordinator_failure() -> None:
    coordinator = FailingCoordinator()
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
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Dispatch ready planner steps.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Dispatch to unavailable expert.",
                    status="pending",
                    template_id="expert-reviewer",
                    required_capabilities=("architecture-review",),
                ),
            ),
        ),
    )

    report = runtime.dispatch_ready_steps("plan_1")

    persisted = runtime.get_plan("plan_1")
    assert report.assigned == ()
    assert [(skip.step_id, skip.reason, skip.detail) for skip in report.skipped] == [
        ("step_1", "dispatch-failed", "expert unavailable"),
    ]
    assert persisted.steps[0].status == "failed"
    assert persisted.steps[0].task_id is not None
    assert persisted.steps[0].error == "expert unavailable"
    assert persisted.assignments[0].dispatch_status == "failed"
    assert persisted.assignments[0].dispatch_error == "expert unavailable"


def test_planner_runtime_dispatch_failure_does_not_overwrite_concurrent_step_completion() -> (
    None
):
    class CompletingFailingCoordinator(FakeCoordinator):
        def dispatch(self, **kwargs: object) -> None:
            self.dispatch_calls.append(kwargs)
            current = store.get_plan("plan_1")
            assert current is not None
            completed_step = replace(
                current.steps[0],
                status="completed",
                error=None,
            )
            store.save_plan(
                replace(
                    current,
                    steps=(completed_step,),
                    status="completed",
                    updated_at=11.0,
                ),
            )
            raise RuntimeError("expert unavailable")

    store = InMemoryPlanStore()
    coordinator = CompletingFailingCoordinator()
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
        clock=lambda: 10.0,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Dispatch ready planner steps.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Dispatch to unavailable expert.",
                    status="pending",
                    template_id="expert-reviewer",
                ),
            ),
        ),
    )

    report = runtime.dispatch_ready_steps("plan_1")

    persisted = runtime.get_plan("plan_1")
    assert [(skip.step_id, skip.reason, skip.detail) for skip in report.skipped] == [
        ("step_1", "dispatch-failed", "expert unavailable"),
    ]
    assert persisted.status == "completed"
    assert persisted.steps[0].status == "completed"
    assert persisted.steps[0].error is None
