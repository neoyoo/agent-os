from __future__ import annotations

import json

import pytest

from agentos.capabilities import ToolRegistry
from agentos.planning import (
    AllowAllPlannerToolAuthorizationPolicy,
    InMemoryPlanStore,
    PlanNotFoundError,
    PlanRetryPolicy,
    PlanStep,
    PlannerRuntime,
    PlannerTools,
    SubAgentTemplate,
)

from tests.planning._async import async_test
from tests.planning._tool_fixtures import FakePlanStepDispatcher, ManualClock
from tests.tool_invocation import call_tool


@async_test
async def test_planner_tools_dispatch_ready_steps_handler_returns_report() -> None:
    registry = ToolRegistry()
    dispatcher = FakePlanStepDispatcher()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=dispatcher,
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
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)
    await call_tool(
        registry.get("plan_create"),
        {"objective": "Review planner dispatch."},
    )
    await call_tool(
        registry.get("plan_add_step"),
        {
            "plan_id": "plan_1",
            "instruction": "Review ready dispatch.",
            "required_capabilities": ["review"],
        },
    )

    report = json.loads(
        await call_tool(
            registry.get("plan_dispatch_ready_steps"),
            {
                "plan_id": "plan_1",
                "default_template_id": "reviewer",
                "limit": 1,
            },
        ),
    )

    assert report == {
        "plan_id": "plan_1",
        "assigned": [
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
                "template_id": "reviewer",
                "task_id": "task_1",
                "target_agent_id": "subagent_1",
                "created_at": 10.0,
                "dispatch_status": "submitted",
                "submitted_at": 10.0,
                "dispatch_error": None,
            },
        ],
        "skipped": [],
    }
    persisted = await runtime.get_plan("plan_1")
    assert persisted.steps[0].status == "assigned"
    assert dispatcher.submit_calls[0].assignment.task_id == "task_1"
    assert dispatcher.submit_calls[0].template.allowed_tool_names == ("read_file",)


@async_test
async def test_planner_tools_scheduler_tick_handler_returns_retry_and_dispatch_report() -> (
    None
):
    registry = ToolRegistry()
    clock = ManualClock(10.0)
    dispatcher = FakePlanStepDispatcher()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=dispatcher,
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
    plan = await runtime.create_plan(
        objective="Run scheduler tick.",
        owner_agent_id="leader",
        plan_id="plan_1",
    )
    await runtime.store.save_plan(
        plan.__class__(
            plan_id="plan_1",
            objective="Run scheduler tick.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Retry due step.",
                    status="failed",
                    template_id="reviewer",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Independent step.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
            created_at=10.0,
            updated_at=10.0,
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    report = json.loads(
        await call_tool(
            registry.get("plan_scheduler_tick"),
            {
                "plan_id": "plan_1",
                "default_template_id": "reviewer",
                "retry_limit": 1,
                "dispatch_limit": 2,
            },
        ),
    )

    assert report["plan_id"] == "plan_1"
    assert report["retry_resets"] == [
        {
            "plan_id": "plan_1",
            "step_id": "step_1",
            "attempts": 1,
        },
    ]
    assert report["dispatch"]["plan_id"] == "plan_1"
    assert report["dispatch"]["skipped"] == []
    assert [assignment["step_id"] for assignment in report["dispatch"]["assigned"]] == [
        "step_1",
        "step_2",
    ]
    for assignment in report["dispatch"]["assigned"]:
        assert assignment["plan_id"] == "plan_1"
        assert assignment["template_id"] == "reviewer"
        assert assignment["task_id"].startswith("task_")
        assert assignment["target_agent_id"].startswith("subagent_")
        assert assignment["created_at"] == 10.0
    assert [call.assignment.task_id for call in dispatcher.submit_calls] == [
        assignment["task_id"] for assignment in report["dispatch"]["assigned"]
    ]


@async_test
async def test_planner_tools_scheduler_tick_cannot_mutate_another_owner_plan() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(store=InMemoryPlanStore(), clock=lambda: 10.0)
    await runtime.create_plan(
        objective="Other owner's private plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(PlanNotFoundError):
        await call_tool(
            registry.get("plan_scheduler_tick"),
            {"plan_id": "other_plan"},
        )
