from __future__ import annotations

from dataclasses import replace

from agentos.multi.planner import (
    EvidenceHandle,
    PlanAssignment,
    PlanState,
    PlanStep,
    plan_to_working_state_summary,
)
from agentos.workspace import WorkspaceHandle


def test_plan_to_working_state_summary_projects_counts_and_next_steps() -> None:
    plan = PlanState(
        plan_id="plan_1",
        objective="Review SDK planner patterns.",
        owner_agent_id="leader",
        status="running",
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="Review intent router.",
                status="completed",
                template_id="reviewer",
                assigned_agent_id="expert",
                task_id="task_secret",
                required_capabilities=("review",),
                evidence_ids=("evidence_1",),
            ),
            PlanStep(
                step_id="step_2",
                instruction="Write plan projection docs.",
                status="pending",
                template_id="writer",
                required_capabilities=("docs",),
                attempts=1,
                next_retry_at=42.0,
                retry_status="scheduled",
            ),
        ),
        evidence=(
            EvidenceHandle(
                evidence_id="evidence_1",
                kind="text",
                summary="Intent router can use a single template-bound step.",
                uri="file:///private/review.md",
                metadata={"path": "/private/review.md"},
            ),
        ),
        assignments=(
            PlanAssignment(
                plan_id="plan_1",
                step_id="step_1",
                template_id="reviewer",
                task_id="task_secret",
                target_agent_id="expert",
                created_at=99.0,
            ),
        ),
        created_at=1.0,
        updated_at=2.0,
        workspace=WorkspaceHandle(
            workspace_id="workspace_1",
            scope="session",
            root="D:/private/workspace",
        ),
    )

    summary = plan_to_working_state_summary(plan)

    assert summary == {
        "plan_id": "plan_1",
        "objective": "Review SDK planner patterns.",
        "status": "running",
        "step_counts": {
            "assigned": 0,
            "blocked": 0,
            "cancelled": 0,
            "completed": 1,
            "failed": 0,
            "pending": 1,
            "running": 0,
        },
        "next_steps": [
            {
                "step_id": "step_2",
                "instruction": "Write plan projection docs.",
                "status": "pending",
                "template_id": "writer",
                "assigned_agent_id": None,
                "required_capabilities": ["docs"],
                "depends_on": [],
                "evidence_ids": [],
                "attempts": 1,
                "next_retry_at": 42.0,
                "retry_status": "scheduled",
            },
        ],
        "recent_evidence": [
            {
                "evidence_id": "evidence_1",
                "kind": "text",
                "summary": "Intent router can use a single template-bound step.",
                "producer_agent_id": None,
            },
        ],
    }


def test_plan_to_working_state_summary_omits_execution_and_storage_details() -> None:
    plan = PlanState(
        plan_id="plan_1",
        objective="Protect execution details.",
        owner_agent_id="leader",
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="Run private task.",
                status="assigned",
                task_id="task_secret",
            ),
        ),
        evidence=(
            EvidenceHandle(
                evidence_id="evidence_1",
                kind="artifact",
                summary="Private artifact.",
                uri="file:///private/artifact.txt",
                metadata={"path": "/private/artifact.txt"},
            ),
        ),
        assignments=(
            PlanAssignment(
                plan_id="plan_1",
                step_id="step_1",
                template_id="worker",
                task_id="task_secret",
                target_agent_id="worker",
                created_at=99.0,
            ),
        ),
        created_at=1.0,
        updated_at=2.0,
        workspace=WorkspaceHandle(
            workspace_id="workspace_1",
            scope="session",
            root="/private/workspace",
        ),
    )

    summary_text = repr(plan_to_working_state_summary(plan))

    assert "task_secret" not in summary_text
    assert "/private" not in summary_text
    assert "file://" not in summary_text
    assert "leader" not in summary_text
    assert "created_at" not in summary_text
    assert "updated_at" not in summary_text
    assert "metadata" not in summary_text
    assert "workspace" not in summary_text


def test_plan_to_working_state_summary_limits_visible_steps_and_evidence() -> None:
    steps = tuple(
        PlanStep(
            step_id=f"step_{index}",
            instruction=f"Step {index}.",
            status="pending",
        )
        for index in range(1, 5)
    )
    evidence = tuple(
        EvidenceHandle(
            evidence_id=f"evidence_{index}",
            kind="text",
            summary=f"Evidence {index}.",
        )
        for index in range(1, 5)
    )
    plan = replace(
        PlanState(
            plan_id="plan_1",
            objective="Limit projection size.",
            owner_agent_id="leader",
        ),
        steps=steps,
        evidence=evidence,
    )

    summary = plan_to_working_state_summary(
        plan,
        max_next_steps=2,
        max_recent_evidence=2,
    )

    assert [step["step_id"] for step in summary["next_steps"]] == [
        "step_1",
        "step_2",
    ]
    assert [
        evidence_item["evidence_id"]
        for evidence_item in summary["recent_evidence"]
    ] == ["evidence_3", "evidence_4"]
    assert summary["step_counts"]["pending"] == 4
