from __future__ import annotations

from agentos.examples.planner_patterns import (
    build_intent_router_example,
    build_plan_and_execute_example,
    main,
)


def test_intent_router_example_returns_plan_summary_projection() -> None:
    summary = build_intent_router_example(
        query="Please review the planner runtime boundary.",
    )

    assert summary["objective"] == "Route request: Please review the planner runtime boundary."
    assert summary["status"] == "draft"
    assert summary["step_counts"]["pending"] == 1
    assert summary["next_steps"] == [
        {
            "step_id": "step_1",
            "instruction": "Handle request as architecture-review.",
            "status": "pending",
            "template_id": "architecture-reviewer",
            "assigned_agent_id": None,
            "required_capabilities": ["architecture-review"],
            "depends_on": [],
            "evidence_ids": [],
            "attempts": 0,
            "next_retry_at": None,
            "retry_status": None,
        },
    ]


def test_plan_and_execute_example_assigns_records_evidence_and_projects_summary() -> None:
    summary = build_plan_and_execute_example()

    assert summary["status"] == "running"
    assert summary["step_counts"]["completed"] == 1
    assert summary["step_counts"]["pending"] == 1
    assert summary["next_steps"][0]["step_id"] == "step_2"
    assert summary["recent_evidence"] == [
        {
            "evidence_id": "evidence_1",
            "kind": "task_result",
            "summary": "Architecture reviewer confirmed the boundary.",
            "producer_agent_id": "architecture_expert",
        },
    ]
    assert "task_dispatch" not in repr(summary)
    assert "task_spawn" not in repr(summary)


def test_planner_patterns_example_has_main_entrypoint(capsys) -> None:
    exit_code = main([])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "intent_router" in output
    assert "plan_and_execute" in output
