from __future__ import annotations

from agentos.examples.planner_patterns import (
    build_intent_router_example,
    build_plan_and_execute_example,
    main,
)
from tests.planning._async import async_test


@async_test
async def test_intent_router_example_returns_canonical_context_projection() -> None:
    context_projection = await build_intent_router_example(
        query="Please review the planner runtime boundary.",
    )

    assert context_projection.startswith(
        '<context-snapshot protocol="agentos.context" version="1.0"',
    )
    assert '<active-plan status="pending">' in context_projection
    assert (
        "<goal>Route request: Please review the planner runtime boundary.</goal>"
        in context_projection
    )
    assert (
        '<step handle="step_1" status="pending">'
        "Handle request as architecture-review.</step>"
        in context_projection
    )
    assert "architecture-reviewer" not in context_projection
    assert "required_capabilities" not in context_projection


@async_test
async def test_plan_and_execute_example_projects_store_truth_through_context_protocol() -> None:
    context_projection = await build_plan_and_execute_example()

    assert '<active-plan status="in-progress">' in context_projection
    assert "<goal>Review planner pattern support.</goal>" in context_projection
    assert (
        '<step handle="step_1" status="completed">'
        "Review the planner runtime boundary.</step>"
        in context_projection
    )
    assert (
        '<step handle="step_2" status="pending">'
        "Summarize the remaining production gaps.</step>"
        in context_projection
    )
    assert "Architecture reviewer confirmed the boundary." not in context_projection
    assert "architecture_expert" not in context_projection
    assert "task_dispatch" not in context_projection
    assert "task_spawn" not in context_projection


@async_test
async def test_planner_patterns_example_has_main_entrypoint(capsys) -> None:
    exit_code = await main([])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "intent_router" in output
    assert "plan_and_execute" in output
    assert "context-snapshot" in output
