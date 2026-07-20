from __future__ import annotations

import json


from agentos.capabilities import ToolRegistry
from agentos.planning import (
    InMemoryPlanStore,
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
async def test_planner_tools_create_add_and_status_handlers() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    created = json.loads(
        await call_tool(
            registry.get("plan_create"),
            {
                "objective": "Review agent-os planner tools.",
            },
        ),
    )
    updated = json.loads(
        await call_tool(
            registry.get("plan_add_step"),
            {
                "plan_id": "plan_1",
                "instruction": "Review tool registration.",
                "required_capabilities": ["architecture-review"],
            },
        ),
    )
    one_plan = json.loads(
        await call_tool(registry.get("plan_status"), {"plan_id": "plan_1"}),
    )
    owner_plans = json.loads(
        await call_tool(registry.get("plan_status"), {}),
    )

    assert created["plan_id"] == "plan_1"
    assert created["owner_agent_id"] == "leader"
    assert updated["steps"][0]["step_id"] == "step_1"
    assert updated["steps"][0]["required_capabilities"] == ["architecture-review"]
    assert one_plan["plan_id"] == "plan_1"
    assert owner_plans["plans"][0]["plan_id"] == "plan_1"


@async_test
async def test_planner_tools_create_from_decomposition_handler() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
                capabilities=("research",),
            ),
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review plan.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    created = json.loads(
        await call_tool(
            registry.get("plan_create_from_decomposition"),
            {
                "objective": "Review AgentOS planner architecture.",
                "plan_id": "plan_decomposed",
                "steps": [
                    {
                        "step_id": "collect",
                        "instruction": "Collect planner requirements.",
                        "required_capabilities": ["research"],
                        "template_id": "researcher",
                    },
                    {
                        "step_id": "review",
                        "instruction": "Review planner risks.",
                        "depends_on": ["collect"],
                        "required_capabilities": ["review"],
                        "template_id": "reviewer",
                    },
                ],
            },
        ),
    )

    assert created["plan_id"] == "plan_decomposed"
    assert created["steps"][0]["instruction"] == "Collect planner requirements."
    assert created["steps"][0]["required_capabilities"] == ["research"]
    assert created["steps"][0]["template_id"] == "researcher"
    assert created["steps"][1]["depends_on"] == ["collect"]
    assert created["steps"][1]["template_id"] == "reviewer"


@async_test
async def test_planner_tools_gate_decomposition_proposal_handler_is_read_only() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
        ),
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    report = json.loads(
        await call_tool(
            registry.get("plan_gate_decomposition_proposal"),
            {
                "proposal": {
                    "objective": "Review AgentOS planner architecture.",
                    "steps": [
                        {
                            "step_id": "collect",
                            "instruction": "Collect planner requirements.",
                            "template_id": "researcher",
                        },
                    ],
                },
                "policy": {
                    "max_steps": 2,
                    "require_template": True,
                    "allowed_template_ids": ["researcher"],
                },
                "metadata": {
                    "source": "unit-test",
                },
            },
        ),
    )

    assert report["accepted"] is True
    assert report["errors"] == []
    assert report["step_count"] == 1
    assert report["required_templates"] == ["researcher"]
    assert report["normalized_decomposition"]["objective"] == (
        "Review AgentOS planner architecture."
    )
    assert report["metadata"] == {"source": "unit-test"}
    assert await runtime.list_plans() == []


@async_test
async def test_planner_tools_gate_decomposition_proposal_handler_reports_policy_errors() -> (
    None
):
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
        ),
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    report = json.loads(
        await call_tool(
            registry.get("plan_gate_decomposition_proposal"),
            {
                "proposal": {
                    "objective": "Review AgentOS planner architecture.",
                    "steps": [
                        {
                            "step_id": "collect",
                            "instruction": "Collect planner requirements.",
                            "template_id": "researcher",
                        },
                    ],
                },
                "policy": {
                    "require_approval": True,
                },
            },
        ),
    )

    assert report["accepted"] is False
    assert report["requires_approval"] is True
    assert report["errors"] == ["decomposition approval is required"]
    assert report["normalized_decomposition"] is None
    assert await runtime.list_plans() == []


@async_test
async def test_planner_tools_ready_steps_handler_respects_dependencies() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    plan = await runtime.create_plan(
        objective="Review DAG planner tools.",
        owner_agent_id="leader",
        plan_id="plan_1",
    )
    await runtime.store.save_plan(
        plan.__class__(
            plan_id=plan.plan_id,
            objective=plan.objective,
            owner_agent_id=plan.owner_agent_id,
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
                    instruction="Write summary.",
                    status="pending",
                    depends_on=("step_2",),
                ),
            ),
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        ),
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    ready = json.loads(
        await call_tool(registry.get("plan_ready_steps"), {"plan_id": "plan_1"}),
    )

    assert [step["step_id"] for step in ready["ready_steps"]] == ["step_2"]
    assert ready["ready_steps"][0]["depends_on"] == ["step_1"]
    assert ready["ready_steps"][0]["template_id"] == "reviewer"


@async_test
async def test_planner_tools_assign_record_evidence_and_complete_handlers() -> None:
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
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)
    await call_tool(
        registry.get("plan_create"),
        {"objective": "Review planner tools."},
    )
    await call_tool(
        registry.get("plan_add_step"),
        {
            "plan_id": "plan_1",
            "instruction": "Review implementation.",
            "template_id": "reviewer",
        },
    )

    assigned = json.loads(
        await call_tool(
            registry.get("plan_assign_step"),
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
                "template_id": "reviewer",
            },
        ),
    )
    evidence = json.loads(
        await call_tool(
            registry.get("plan_record_evidence"),
            {
                "plan_id": "plan_1",
                "step_ids": ["step_1"],
                "kind": "text",
                "summary": "Planner tools preserve runtime boundary.",
                "uri": "memory://evidence/1",
                "producer_agent_id": "subagent_1",
                "metadata": {"source": "unit-test"},
            },
        ),
    )
    completed = json.loads(
        await call_tool(
            registry.get("plan_complete_step"),
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
                "evidence_ids": ["evidence_1"],
            },
        ),
    )

    assert assigned["steps"][0]["status"] == "assigned"
    assert assigned["assignments"][0]["task_id"] == "task_1"
    assert dispatcher.submit_calls[0].assignment.task_id == "task_1"
    assert dispatcher.submit_calls[0].template.allowed_tool_names == ("read_file",)
    assert evidence["evidence_id"] == "evidence_1"
    assert evidence["metadata"] == {"source": "unit-test"}
    assert completed["steps"][0]["status"] == "completed"
    assert completed["status"] == "completed"


@async_test
async def test_planner_tools_fail_retryable_and_retry_handlers() -> None:
    registry = ToolRegistry()
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)
    await call_tool(
        registry.get("plan_create"),
        {
            "objective": "Review planner recovery tools.",
            "plan_id": "plan_1",
        },
    )
    await call_tool(
        registry.get("plan_add_step"),
        {
            "plan_id": "plan_1",
            "instruction": "Run worker.",
        },
    )

    clock.value = 20.0
    failed = json.loads(
        await call_tool(
            registry.get("plan_fail_step"),
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
                "error": "worker crashed",
            },
        ),
    )
    clock.value = 25.0
    retryable = json.loads(
        await call_tool(
            registry.get("plan_retryable_steps"),
            {"plan_id": "plan_1"},
        ),
    )
    retried = json.loads(
        await call_tool(
            registry.get("plan_retry_step"),
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
            },
        ),
    )

    assert failed["steps"][0]["status"] == "failed"
    assert failed["steps"][0]["error"] == "worker crashed"
    assert failed["steps"][0]["attempts"] == 1
    assert failed["steps"][0]["retry_status"] == "scheduled"
    assert failed["steps"][0]["next_retry_at"] == 25.0
    assert [step["step_id"] for step in retryable["retryable_steps"]] == ["step_1"]
    assert retryable["retryable_steps"][0]["attempts"] == 1
    assert retried["steps"][0]["status"] == "pending"
    assert retried["steps"][0]["error"] is None
    assert retried["steps"][0]["attempts"] == 1
    assert retried["steps"][0]["retry_status"] is None
