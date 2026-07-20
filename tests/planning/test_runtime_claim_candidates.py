from __future__ import annotations

from tests.planning._async import async_test


import pytest

from agentos.planning import (
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanState,
    PlanStep,
    PlannerRuntime,
)


@async_test
async def test_planner_runtime_claims_schedulable_plans_with_owner_status_limit() -> (
    None
):
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        clock=lambda: 10.0,
    )
    await runtime.store.create_plan(
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
    await runtime.store.create_plan(
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
    await runtime.store.create_plan(
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
    await runtime.store.create_plan(
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

    first_claims = await runtime.claim_schedulable_plans(
        owner_agent_id="leader",
        statuses=("running",),
        worker_id="scheduler_a",
        lease_seconds=20.0,
        limit=1,
    )
    second_worker_claims = await runtime.claim_schedulable_plans(
        owner_agent_id="leader",
        statuses=("running",),
        worker_id="scheduler_b",
        lease_seconds=20.0,
        limit=2,
    )

    assert [result.status for result in first_claims] == ["claimed"]
    assert first_claims[0].claim is not None
    assert first_claims[0].claim.as_dict() == {
        "plan_id": "first_running",
        "owner_agent_id": "leader",
        "worker_id": "scheduler_a",
        "claimed_at": 10.0,
        "lease_expires_at": 30.0,
        "generation": 1,
    }
    assert [result.status for result in second_worker_claims] == [
        "busy",
        "claimed",
    ]
    assert second_worker_claims[0].existing_claim is not None
    assert second_worker_claims[0].existing_claim.worker_id == "scheduler_a"
    assert second_worker_claims[1].claim is not None
    assert second_worker_claims[1].claim.plan_id == "second_running"
    assert runtime.claim_store is not None
    assert await runtime.claim_store.get_claim("draft_plan") is None
    assert await runtime.claim_store.get_claim("other_owner") is None


@async_test
async def test_planner_runtime_claim_schedulable_plans_requires_claim_store() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    with pytest.raises(RuntimeError, match="claim_store"):
        await runtime.claim_schedulable_plans(
            worker_id="scheduler",
            lease_seconds=20.0,
        )
