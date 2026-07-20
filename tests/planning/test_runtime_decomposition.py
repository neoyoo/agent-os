from __future__ import annotations

from tests.planning._async import async_test


import pytest

from agentos.planning import (
    InMemoryPlanStore,
    PlanDecomposition,
    PlanState,
    PlanStep,
    PlanStepSpec,
    PlannerRuntime,
    SubAgentTemplate,
)


@async_test
async def test_planner_runtime_creates_plan_from_structured_decomposition() -> None:
    id_counts = {"plan": 0, "step": 0}

    def next_id(prefix: str) -> str:
        id_counts[prefix] += 1
        return f"{prefix}_{id_counts[prefix]}"

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
                role="Review findings.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 10.0,
        id_factory=next_id,
    )
    decomposition = PlanDecomposition(
        objective="Review AgentOS planner architecture.",
        steps=(
            PlanStepSpec(
                instruction="Collect planner requirements and current gaps.",
                required_capabilities=("research",),
                template_id="researcher",
            ),
            PlanStepSpec(
                instruction="Review proposed plan for production risks.",
                required_capabilities=("review",),
                template_id="reviewer",
                depends_on=("step_1",),
            ),
        ),
    )

    plan = await runtime.create_plan_from_decomposition(
        decomposition,
        owner_agent_id="leader",
        plan_id="plan_from_decomposition",
    )

    assert plan.plan_id == "plan_from_decomposition"
    assert plan.objective == "Review AgentOS planner architecture."
    assert plan.status == "draft"
    assert [step.step_id for step in plan.steps] == ["step_1", "step_2"]
    assert [step.template_id for step in plan.steps] == ["researcher", "reviewer"]
    assert [step.depends_on for step in plan.steps] == [(), ("step_1",)]
    assert [step.required_capabilities for step in plan.steps] == [
        ("research",),
        ("review",),
    ]


@async_test
async def test_planner_runtime_rejects_invalid_decomposition() -> None:
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

    with pytest.raises(
        ValueError, match="decomposition must include at least one step"
    ):
        await runtime.create_plan_from_decomposition(
            PlanDecomposition(
                objective="Empty plan.",
                steps=(),
            ),
            owner_agent_id="leader",
        )


@async_test
async def test_planner_runtime_validates_decomposition_without_creating_plan() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
        ),
        id_factory=lambda prefix: f"{prefix}_1",
    )

    report = runtime.validate_decomposition(
        PlanDecomposition(
            objective="Review planner decomposition policy.",
            steps=(
                PlanStepSpec(
                    instruction="Collect evidence.",
                    step_id="collect",
                    template_id="researcher",
                ),
                PlanStepSpec(
                    instruction="Write synthesis.",
                    step_id="write",
                    depends_on=("collect",),
                ),
            ),
        ),
    )

    assert report.ok is True
    assert report.errors == ()
    assert report.step_count == 2
    assert report.required_templates == ("researcher",)
    assert report.unknown_templates == ()
    assert report.as_dict()["ok"] is True
    assert await runtime.list_plans() == []


@async_test
async def test_planner_runtime_validation_reports_decomposition_errors() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="known",
                name="Known",
                role="Known worker.",
            ),
        ),
    )

    report = runtime.validate_decomposition(
        PlanDecomposition(
            objective="Invalid plan.",
            steps=(
                PlanStepSpec(
                    instruction="Use missing template.",
                    step_id="first",
                    template_id="missing",
                ),
                PlanStepSpec(
                    instruction="Duplicate id.",
                    step_id="first",
                    depends_on=("unknown",),
                ),
            ),
        ),
    )

    assert report.ok is False
    assert report.step_count == 2
    assert report.required_templates == ("missing",)
    assert report.unknown_templates == ("missing",)
    assert any("unknown template" in error for error in report.errors)
    assert await runtime.list_plans() == []


@async_test
async def test_planner_runtime_validation_does_not_consume_generated_step_ids() -> None:
    id_counts = {"plan": 0, "step": 0}

    def next_id(prefix: str) -> str:
        id_counts[prefix] += 1
        return f"{prefix}_{id_counts[prefix]}"

    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        id_factory=next_id,
    )
    decomposition = PlanDecomposition(
        objective="Validate before creation.",
        steps=(
            PlanStepSpec(instruction="First generated step."),
            PlanStepSpec(instruction="Second generated step."),
        ),
    )

    report = runtime.validate_decomposition(decomposition)
    plan = await runtime.create_plan_from_decomposition(
        decomposition,
        owner_agent_id="leader",
    )

    assert report.ok is True
    assert [step.step_id for step in plan.steps] == ["step_1", "step_2"]


@async_test
async def test_planner_runtime_lists_ready_steps_after_dependencies_complete() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    plan = PlanState(
        plan_id="plan_1",
        objective="Review DAG scheduling.",
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
            ),
            PlanStep(
                step_id="step_3",
                instruction="Write final synthesis.",
                status="pending",
                depends_on=("step_2",),
            ),
            PlanStep(
                step_id="step_4",
                instruction="Already assigned.",
                status="assigned",
            ),
        ),
    )
    await runtime.store.create_plan(plan)

    ready = await runtime.ready_steps("plan_1")

    assert [step.step_id for step in ready] == ["step_2"]


@async_test
async def test_planner_runtime_rejects_cyclic_decomposition_dependencies() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        id_factory=lambda prefix: f"{prefix}_1",
    )

    with pytest.raises(ValueError, match="cycle"):
        await runtime.create_plan_from_decomposition(
            PlanDecomposition(
                objective="Cyclic plan.",
                steps=(
                    PlanStepSpec(
                        instruction="First step.",
                        step_id="step_a",
                        depends_on=("step_b",),
                    ),
                    PlanStepSpec(
                        instruction="Second step.",
                        step_id="step_b",
                        depends_on=("step_a",),
                    ),
                ),
            ),
            owner_agent_id="leader",
        )
    with pytest.raises(KeyError):
        await runtime.create_plan_from_decomposition(
            PlanDecomposition(
                objective="Unknown template.",
                steps=(
                    PlanStepSpec(
                        instruction="Use unknown template.",
                        template_id="missing",
                    ),
                ),
            ),
            owner_agent_id="leader",
        )
