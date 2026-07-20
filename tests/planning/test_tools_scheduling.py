from __future__ import annotations

import json

import pytest

from agentos.capabilities import ToolRegistry
from agentos.planning import (
    AllowAllPlannerToolAuthorizationPolicy,
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanRetryPolicy,
    PlanState,
    PlanStep,
    PlannerRuntime,
    PlannerTools,
    SubAgentTemplate,
)

from tests.planning._async import async_test
from tests.planning._tool_fixtures import FakePlanStepDispatcher, ManualClock
from tests.tool_invocation import call_tool


@async_test
async def test_planner_tools_schedulable_plans_handler_returns_owner_scoped_summaries() -> (
    None
):
    registry = ToolRegistry()
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="leader_running",
            objective="Leader running plan.",
            owner_agent_id="leader",
            status="running",
            updated_at=5.0,
            steps=(
                PlanStep(
                    step_id="ready_step",
                    instruction="Run leader work.",
                    status="pending",
                ),
            ),
        ),
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="leader_draft",
            objective="Leader draft plan.",
            owner_agent_id="leader",
            status="draft",
            updated_at=6.0,
            steps=(
                PlanStep(
                    step_id="draft_step",
                    instruction="Run draft work.",
                    status="pending",
                ),
            ),
        ),
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="other_running",
            objective="Other owner running plan.",
            owner_agent_id="other",
            status="running",
            updated_at=7.0,
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Run other owner work.",
                    status="pending",
                ),
            ),
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        await call_tool(
            registry.get("plan_schedulable_plans"),
            {
                "statuses": ["running"],
                "limit": 5,
            },
        ),
    )

    assert payload == {
        "plans": [
            {
                "plan_id": "leader_running",
                "owner_agent_id": "leader",
                "status": "running",
                "ready_step_ids": ["ready_step"],
                "retryable_step_ids": [],
                "reasons": ["ready-steps"],
                "updated_at": 5.0,
            },
        ],
    }


@async_test
async def test_planner_tools_schedulable_plans_handler_reports_due_retries() -> None:
    registry = ToolRegistry()
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="retry_plan",
            objective="Retry due plan.",
            owner_agent_id="leader",
            status="running",
            updated_at=8.0,
            steps=(
                PlanStep(
                    step_id="retry_step",
                    instruction="Retry due work.",
                    status="failed",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
            ),
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        await call_tool(registry.get("plan_schedulable_plans"), {}),
    )

    assert payload["plans"][0]["plan_id"] == "retry_plan"
    assert payload["plans"][0]["retryable_step_ids"] == ["retry_step"]
    assert payload["plans"][0]["reasons"] == ["due-retries"]


@async_test
async def test_planner_tools_claim_schedulable_plans_handler_returns_owner_scoped_claims() -> (
    None
):
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        clock=lambda: 10.0,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="leader_running",
            objective="Leader running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="ready_step",
                    instruction="Run leader work.",
                    status="pending",
                ),
            ),
        ),
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="other_running",
            objective="Other owner running plan.",
            owner_agent_id="other",
            status="running",
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Run other owner work.",
                    status="pending",
                ),
            ),
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        await call_tool(
            registry.get("plan_claim_schedulable_plans"),
            {
                "worker_id": "scheduler_a",
                "lease_seconds": 20.0,
                "statuses": ["running"],
                "limit": 1,
            },
        ),
    )

    assert payload == {
        "claims": [
            {
                "status": "claimed",
                "claim": {
                    "plan_id": "leader_running",
                    "owner_agent_id": "leader",
                    "worker_id": "scheduler_a",
                    "claimed_at": 10.0,
                    "lease_expires_at": 30.0,
                    "generation": 1,
                },
                "existing_claim": None,
            },
        ],
    }
    assert runtime.claim_store is not None
    assert await runtime.claim_store.get_claim("other_running") is None


@async_test
async def test_planner_tools_claim_schedulable_plans_handler_reports_busy_claims() -> (
    None
):
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        clock=lambda: 10.0,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="leader_running",
            objective="Leader running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="ready_step",
                    instruction="Run leader work.",
                    status="pending",
                ),
            ),
        ),
    )
    await runtime.claim_store.claim_plan(
        plan_id="leader_running",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=20.0,
        now=10.0,
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        await call_tool(
            registry.get("plan_claim_schedulable_plans"),
            {
                "worker_id": "scheduler_b",
                "lease_seconds": 20.0,
                "statuses": ["running"],
                "limit": 1,
            },
        ),
    )

    assert payload["claims"][0]["status"] == "busy"
    assert payload["claims"][0]["claim"] is None
    assert payload["claims"][0]["existing_claim"]["worker_id"] == "scheduler_a"


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        (
            {
                "worker_id": "",
                "lease_seconds": 20.0,
            },
            "worker_id",
        ),
        (
            {
                "worker_id": "scheduler",
                "lease_seconds": 0,
            },
            "lease_seconds",
        ),
    ],
)
@async_test
async def test_planner_tools_claim_schedulable_plans_handler_rejects_invalid_arguments(
    arguments: dict[str, object],
    match: str,
) -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(ValueError, match=match):
        await call_tool(
            registry.get("plan_claim_schedulable_plans"),
            arguments,
        )


@async_test
async def test_planner_tools_claimed_scheduler_tick_handler_ticks_only_claimed_plans() -> (
    None
):
    registry = ToolRegistry()
    claim_store = InMemoryPlanClaimStore()
    dispatcher = FakePlanStepDispatcher()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=dispatcher,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=lambda: 10.0,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="leader_busy",
            objective="Busy leader plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="busy_step",
                    instruction="Do not dispatch this.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="leader_free",
            objective="Free leader plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="free_step",
                    instruction="Dispatch this.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="other_free",
            objective="Other owner plan.",
            owner_agent_id="other",
            status="running",
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Do not select this.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    await claim_store.claim_plan(
        plan_id="leader_busy",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        now=10.0,
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        await call_tool(
            registry.get("plan_claimed_scheduler_tick"),
            {
                "worker_id": "scheduler_b",
                "lease_seconds": 30.0,
                "default_template_id": "reviewer",
                "release_after_tick": True,
            },
        ),
    )

    assert payload["worker_id"] == "scheduler_b"
    assert [claim["status"] for claim in payload["claims"]] == [
        "busy",
        "claimed",
    ]
    assert payload["skipped"][0]["plan_id"] == "leader_busy"
    assert payload["skipped"][0]["reason"] == "busy"
    assert payload["tick_reports"][0]["plan_id"] == "leader_free"
    assert payload["released_plan_ids"] == ["leader_free"]
    assert (await runtime.get_plan("leader_busy")).steps[0].status == "pending"
    assert (await runtime.get_plan("leader_free")).steps[0].status == "assigned"
    assert (await runtime.get_plan("other_free")).steps[0].status == "pending"
    assert await claim_store.get_claim("leader_free") is None
    assert len(dispatcher.submit_calls) == 1


@async_test
async def test_planner_tools_claimed_scheduler_tick_handler_rejects_invalid_arguments() -> (
    None
):
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(ValueError, match="worker_id"):
        await call_tool(
            registry.get("plan_claimed_scheduler_tick"),
            {
                "worker_id": "",
                "lease_seconds": 30.0,
            },
        )
    with pytest.raises(ValueError, match="lease_seconds"):
        await call_tool(
            registry.get("plan_claimed_scheduler_tick"),
            {
                "worker_id": "scheduler",
                "lease_seconds": 0.0,
            },
        )
