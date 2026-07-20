from __future__ import annotations

import json

import pytest

from agentos.capabilities import ToolRegistry
from agentos.planning import (
    AllowAllPlannerToolAuthorizationPolicy,
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanNotFoundError,
    PlanState,
    PlanStep,
    PlannerToolAuthorizationError,
    PlannerRuntime,
    PlannerTools,
    SubAgentTemplate,
)

from agentos.planning.tools import PlannerTools as CanonicalPlannerTools
from tests.planning._async import async_test
from tests.planning._tool_fixtures import FakePlanStepDispatcher, StatusRuntimeSpy


def test_planner_tools_have_one_canonical_identity() -> None:
    assert PlannerTools is CanonicalPlannerTools


def test_planner_tools_register_external_tools() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    names = [spec["function"]["name"] for spec in registry.provider_tool_specs()]
    assert names == [
        "plan_create",
        "plan_gate_decomposition_proposal",
        "plan_create_from_decomposition",
        "plan_add_step",
        "plan_assign_step",
        "plan_ready_steps",
        "plan_schedulable_plans",
        "plan_claim_schedulable_plans",
        "plan_claimed_scheduler_tick",
        "plan_dispatch_ready_steps",
        "plan_scheduler_tick",
        "plan_fail_step",
        "plan_retryable_steps",
        "plan_retry_step",
        "plan_record_evidence",
        "plan_complete_step",
        "plan_status",
    ]


@async_test
async def test_planner_tools_default_policy_denies_scheduler_and_dispatch_tools() -> (
    None
):
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        dispatcher=FakePlanStepDispatcher(),
    )
    await runtime.create_plan(
        objective="Review guarded planner tools.",
        owner_agent_id="leader",
        plan_id="plan_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    with pytest.raises(
        PlannerToolAuthorizationError, match="plan_claim_schedulable_plans"
    ):
        await registry.get("plan_claim_schedulable_plans").handler(
            {
                "worker_id": "scheduler",
                "lease_seconds": 30.0,
            },
        )
    with pytest.raises(
        PlannerToolAuthorizationError, match="plan_dispatch_ready_steps"
    ):
        await registry.get("plan_dispatch_ready_steps").handler(
            {"plan_id": "plan_1"},
        )
    with pytest.raises(PlannerToolAuthorizationError, match="plan_scheduler_tick"):
        await registry.get("plan_scheduler_tick").handler({"plan_id": "plan_1"})


@async_test
async def test_planner_tools_allow_all_policy_allows_scheduler_tool_boundary() -> None:
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
            steps=(PlanStep(step_id="ready_step", instruction="Run work."),),
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        await registry.get("plan_claim_schedulable_plans").handler(
            {
                "worker_id": "scheduler_a",
                "lease_seconds": 20.0,
            },
        ),
    )

    assert payload["claims"][0]["status"] == "claimed"


@async_test
async def test_planner_tools_status_cannot_read_another_owner_plan() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    await runtime.create_plan(
        objective="Other owner's private plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_status").handler({"plan_id": "other_plan"})


@async_test
async def test_planner_tools_cannot_mutate_another_owner_plan() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=FakePlanStepDispatcher(),
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
    await runtime.create_plan(
        objective="Other owner's private plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )
    await runtime.add_step("other_plan", instruction="Keep this step private.")
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_add_step").handler(
            {
                "plan_id": "other_plan",
                "instruction": "Mutate private plan.",
            },
        )
    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_assign_step").handler(
            {
                "plan_id": "other_plan",
                "step_id": "step_1",
                "template_id": "reviewer",
            },
        )
    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_record_evidence").handler(
            {
                "plan_id": "other_plan",
                "step_ids": ["step_1"],
                "kind": "text",
                "summary": "Mutate evidence.",
            },
        )
    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_complete_step").handler(
            {
                "plan_id": "other_plan",
                "step_id": "step_1",
            },
        )
    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_fail_step").handler(
            {
                "plan_id": "other_plan",
                "step_id": "step_1",
                "error": "Mutate failure.",
            },
        )
    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_retryable_steps").handler(
            {"plan_id": "other_plan"},
        )
    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_retry_step").handler(
            {
                "plan_id": "other_plan",
                "step_id": "step_1",
            },
        )
    with pytest.raises(PlanNotFoundError):
        await registry.get("plan_dispatch_ready_steps").handler(
            {"plan_id": "other_plan"},
        )

    private_plan = await runtime.get_plan("other_plan")
    assert len(private_plan.steps) == 1
    assert private_plan.steps[0].status == "pending"
    assert private_plan.evidence == ()


@async_test
async def test_planner_tools_status_uses_planner_runtime_query_boundary() -> None:
    registry = ToolRegistry()
    runtime = StatusRuntimeSpy()
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)
    await registry.get("plan_create").handler(
        {
            "objective": "Review planner status boundary.",
            "plan_id": "plan_1",
        },
    )

    await registry.get("plan_status").handler({"plan_id": "plan_1"})
    await registry.get("plan_status").handler({})

    assert runtime.get_plan_calls == [("plan_1", "leader")]
    assert runtime.list_plan_calls == ["leader"]


@async_test
async def test_planner_tools_record_evidence_rejects_unknown_kind() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(store=InMemoryPlanStore(), clock=lambda: 10.0)
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)
    await registry.get("plan_create").handler(
        {
            "objective": "Review evidence validation.",
            "plan_id": "plan_1",
        },
    )

    with pytest.raises(ValueError, match="unsupported evidence kind"):
        await registry.get("plan_record_evidence").handler(
            {
                "plan_id": "plan_1",
                "kind": "unknown",
                "summary": "Invalid kind.",
            },
        )


def test_planner_tools_evidence_kind_schema_is_enumerated() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(store=InMemoryPlanStore(), clock=lambda: 10.0)
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    parameters = registry.get("plan_record_evidence").parameters

    assert parameters["properties"]["kind"]["enum"] == (
        "text",
        "artifact",
        "task_result",
        "team_message",
        "external",
    )
