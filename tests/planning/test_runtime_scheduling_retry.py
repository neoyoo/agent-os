from __future__ import annotations

from tests.planning._async import async_test


import pytest

from agentos.planning import (
    InMemoryPlanStore,
    PlanRetryPolicy,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from agentos.planning.scheduling_reports import (
    PlanSchedulerTickReport,
)
from tests.planning._runtime_fixtures import (
    FakeCoordinator,
    ManualClock,
)


@async_test
async def test_planner_runtime_records_failed_step_and_schedules_retry() -> None:
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(
            max_attempts=3,
            backoff_seconds=5.0,
            backoff_multiplier=2.0,
        ),
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = await runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    await runtime.add_step(plan.plan_id, instruction="Review planner recovery.")

    clock.value = 20.0
    updated = await runtime.fail_step(
        plan.plan_id,
        "step_1",
        error="worker crashed",
    )

    failed_step = updated.steps[0]
    assert updated.status == "running"
    assert failed_step.status == "failed"
    assert failed_step.error == "worker crashed"
    assert failed_step.attempts == 1
    assert failed_step.last_failed_at == 20.0
    assert failed_step.retry_status == "scheduled"
    assert failed_step.next_retry_at == 25.0
    assert failed_step.retry_exhausted_at is None


@async_test
async def test_planner_runtime_lists_due_retryable_steps_and_resets_for_retry() -> None:
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = await runtime.create_plan(
        objective="Review retry flow.", owner_agent_id="leader"
    )
    await runtime.add_step(plan.plan_id, instruction="Run worker.")
    clock.value = 20.0
    await runtime.fail_step(plan.plan_id, "step_1", error="temporary outage")

    clock.value = 24.0
    assert await runtime.retryable_steps(plan.plan_id) == ()

    clock.value = 25.0
    assert [step.step_id for step in await runtime.retryable_steps(plan.plan_id)] == [
        "step_1",
    ]

    retried = await runtime.retry_step(plan.plan_id, "step_1")
    retried_step = retried.steps[0]
    assert retried.status == "running"
    assert retried_step.status == "pending"
    assert retried_step.error is None
    assert retried_step.attempts == 1
    assert retried_step.last_failed_at == 20.0
    assert retried_step.next_retry_at is None
    assert retried_step.retry_status is None
    assert retried_step.retry_exhausted_at is None


@async_test
async def test_planner_runtime_exhausts_failed_step_retries() -> None:
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=1, backoff_seconds=5.0),
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = await runtime.create_plan(
        objective="Review exhausted retry.", owner_agent_id="leader"
    )
    await runtime.add_step(plan.plan_id, instruction="Run worker.")

    clock.value = 20.0
    updated = await runtime.fail_step(plan.plan_id, "step_1", error="permanent failure")

    failed_step = updated.steps[0]
    assert updated.status == "failed"
    assert failed_step.status == "failed"
    assert failed_step.attempts == 1
    assert failed_step.retry_status == "exhausted"
    assert failed_step.next_retry_at is None
    assert failed_step.retry_exhausted_at == 20.0
    assert await runtime.retryable_steps(plan.plan_id) == ()
    with pytest.raises(ValueError, match="not retryable"):
        await runtime.retry_step(plan.plan_id, "step_1")


@async_test
async def test_planner_runtime_scheduler_tick_retries_due_steps_before_dispatch() -> (
    None
):
    clock = ManualClock(10.0)
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
            ),
        ),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Run one planner scheduler pass.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Collect evidence.",
                    status="failed",
                    template_id="reviewer",
                    attempts=1,
                    last_failed_at=5.0,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                    error="temporary outage",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Review evidence.",
                    status="pending",
                    template_id="reviewer",
                    depends_on=("step_1",),
                ),
                PlanStep(
                    step_id="step_3",
                    instruction="Review independent module.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = await runtime.scheduler_tick("plan_1", dispatch_limit=2)

    persisted = await runtime.get_plan("plan_1")
    assert isinstance(report, PlanSchedulerTickReport)
    assert [reset.step_id for reset in report.retry_resets] == ["step_1"]
    assert report.retry_resets[0].attempts == 1
    assert [assignment.step_id for assignment in report.dispatch.assigned] == [
        "step_1",
        "step_3",
    ]
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[0].error is None
    assert persisted.steps[0].retry_status is None
    assert persisted.steps[1].status == "pending"
    assert persisted.steps[2].status == "assigned"
    assert len(coordinator.spawn_calls) == 2


@async_test
async def test_planner_runtime_scheduler_tick_respects_retry_and_dispatch_limits() -> (
    None
):
    clock = ManualClock(10.0)
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
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Bound scheduler work.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Retry first.",
                    status="failed",
                    template_id="reviewer",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Retry second.",
                    status="failed",
                    template_id="reviewer",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
                PlanStep(
                    step_id="step_3",
                    instruction="Independent ready step.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = await runtime.scheduler_tick(
        "plan_1",
        retry_limit=1,
        dispatch_limit=1,
    )

    persisted = await runtime.get_plan("plan_1")
    assert [reset.step_id for reset in report.retry_resets] == ["step_1"]
    assert [assignment.step_id for assignment in report.dispatch.assigned] == [
        "step_1",
    ]
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[1].status == "failed"
    assert persisted.steps[1].retry_status == "scheduled"
    assert persisted.steps[2].status == "pending"


@async_test
async def test_planner_runtime_scheduler_tick_rejects_invalid_limits() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    with pytest.raises(ValueError, match="retry_limit"):
        await runtime.scheduler_tick("plan_1", retry_limit=0)
    with pytest.raises(ValueError, match="dispatch_limit"):
        await runtime.scheduler_tick("plan_1", dispatch_limit=0)
