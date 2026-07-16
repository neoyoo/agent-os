from __future__ import annotations


import pytest

from agentos.planning import (
    InMemoryPlanStore,
    PlanRetryPolicy,
    PlanState,
    PlanStep,
    PlannerRuntime,
)
from tests.planning._runtime_fixtures import (
    ManualClock,
)


def test_planner_runtime_lists_schedulable_plans_by_ready_or_retryable_steps() -> None:
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="ready_plan",
            objective="Has ready work.",
            owner_agent_id="leader",
            status="draft",
            updated_at=3.0,
            steps=(
                PlanStep(
                    step_id="ready_step",
                    instruction="Run ready work.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="retry_plan",
            objective="Has due retry work.",
            owner_agent_id="leader",
            status="running",
            updated_at=5.0,
            steps=(
                PlanStep(
                    step_id="retry_step",
                    instruction="Retry failed work.",
                    status="failed",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="blocked_plan",
            objective="Blocked by dependency.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="blocked_step",
                    instruction="Wait for dependency.",
                    status="pending",
                    depends_on=("missing_completion",),
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="assigned_plan",
            objective="Already assigned.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="assigned_step",
                    instruction="Already assigned.",
                    status="assigned",
                ),
            ),
        ),
    )

    summaries = runtime.schedulable_plans(owner_agent_id="leader")

    assert [summary.plan_id for summary in summaries] == [
        "ready_plan",
        "retry_plan",
    ]
    assert summaries[0].ready_step_ids == ("ready_step",)
    assert summaries[0].retryable_step_ids == ()
    assert summaries[0].reasons == ("ready-steps",)
    assert summaries[0].as_dict() == {
        "plan_id": "ready_plan",
        "owner_agent_id": "leader",
        "status": "draft",
        "ready_step_ids": ["ready_step"],
        "retryable_step_ids": [],
        "reasons": ["ready-steps"],
        "updated_at": 3.0,
    }
    assert summaries[1].ready_step_ids == ()
    assert summaries[1].retryable_step_ids == ("retry_step",)
    assert summaries[1].reasons == ("due-retries",)


def test_planner_runtime_schedulable_plans_filters_owner_status_and_limit() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore(), clock=lambda: 10.0)
    runtime.store.create_plan(
        PlanState(
            plan_id="first_running",
            objective="First running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="first_step",
                    instruction="Run first.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="other_owner",
            objective="Other owner plan.",
            owner_agent_id="other",
            status="running",
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Run other.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="draft_plan",
            objective="Draft plan.",
            owner_agent_id="leader",
            status="draft",
            steps=(
                PlanStep(
                    step_id="draft_step",
                    instruction="Run draft.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="completed_plan",
            objective="Completed plan should not be selected by default.",
            owner_agent_id="leader",
            status="completed",
            steps=(
                PlanStep(
                    step_id="completed_ready",
                    instruction="Would be ready if status allowed.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="second_running",
            objective="Second running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="second_step",
                    instruction="Run second.",
                    status="pending",
                ),
            ),
        ),
    )

    running_only = runtime.schedulable_plans(
        owner_agent_id="leader",
        statuses=("running",),
        limit=1,
    )
    with_completed = runtime.schedulable_plans(
        owner_agent_id="leader",
        statuses=("completed",),
    )

    assert [summary.plan_id for summary in running_only] == ["first_running"]
    assert [summary.plan_id for summary in with_completed] == ["completed_plan"]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": -1}, "limit"),
        ({"statuses": ()}, "statuses"),
        ({"statuses": ("running", "unknown")}, "unsupported plan status"),
        ({"statuses": ("",)}, "unsupported plan status"),
    ],
)
def test_planner_runtime_schedulable_plans_rejects_invalid_filters(
    kwargs: dict[str, object],
    match: str,
) -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    with pytest.raises(ValueError, match=match):
        runtime.schedulable_plans(**kwargs)
