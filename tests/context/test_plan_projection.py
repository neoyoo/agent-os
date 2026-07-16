from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from inspect import signature

import pytest

from agentos.context.models import ContextSlotProjection
from agentos.context.snapshot import ContextSnapshotRenderer
from agentos.planning.errors import PlanNotFoundError, PlanProjectionError
from agentos.planning.models import (
    EvidenceHandle,
    PlanAssignment,
    PlanState,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
)
from agentos.planning.projection import (
    AuthorizedPlanSource,
    BoundPlanProjectionProvider,
)
from agentos.workspace import WorkspaceHandle
from tests.context._snapshot_fixtures import RecordingTokenCounter


class MutablePlanStore:
    def __init__(self, plans: tuple[PlanState, ...] = ()) -> None:
        self.plans = {plan.plan_id: plan for plan in plans}
        self.reads: list[str] = []

    def get_plan(self, plan_id: str) -> PlanState | None:
        self.reads.append(plan_id)
        return self.plans.get(plan_id)


def plan(
    *,
    status: PlanStatus = "running",
    steps: tuple[PlanStep, ...] = (PlanStep("step_1", "Do work."),),
    objective: str = "Complete the task.",
) -> PlanState:
    return PlanState(
        plan_id="plan_1",
        objective=objective,
        owner_agent_id="agent_1",
        status=status,
        steps=steps,
    )


def project(current: PlanState) -> tuple[ContextSlotProjection, ...]:
    store = MutablePlanStore((current,))
    return BoundPlanProjectionProvider(
        AuthorizedPlanSource(store),  # type: ignore[arg-type]
        current.plan_id,
        current.owner_agent_id,
    ).projections()


def test_authorized_plan_source_hides_missing_and_owner_mismatch() -> None:
    store = MutablePlanStore((plan(),))
    source = AuthorizedPlanSource(store)  # type: ignore[arg-type]

    assert source.get_for_projection("plan_1", "agent_1").plan_id == "plan_1"
    with pytest.raises(PlanNotFoundError) as wrong_owner:
        source.get_for_projection("plan_1", "other_agent")
    with pytest.raises(PlanNotFoundError) as missing:
        source.get_for_projection("missing", "other_agent")

    assert str(wrong_owner.value) == str(missing.value)
    assert store.reads == ["plan_1", "plan_1", "missing"]


def test_bound_provider_freezes_scope_and_reads_truth_for_every_projection() -> None:
    store = MutablePlanStore((plan(objective="First objective."),))
    provider = BoundPlanProjectionProvider(
        AuthorizedPlanSource(store),  # type: ignore[arg-type]
        "plan_1",
        "agent_1",
    )

    first = provider.projections()[0]
    store.plans["plan_1"] = replace(
        store.plans["plan_1"],
        objective="Updated objective.",
    )
    second = provider.projections()[0]

    assert first.variants[0].element.children[0].text == "First objective."
    assert second.variants[0].element.children[0].text == "Updated objective."
    assert store.reads == ["plan_1", "plan_1"]
    assert tuple(signature(BoundPlanProjectionProvider.projections).parameters) == (
        "self",
    )
    with pytest.raises(FrozenInstanceError):
        provider.plan_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ((PlanStep("pending", "Pending.", status="pending"),), "in-progress"),
        ((PlanStep("assigned", "Assigned.", status="assigned"),), "in-progress"),
        ((PlanStep("running", "Running.", status="running"),), "in-progress"),
        ((PlanStep("blocked", "Blocked.", status="blocked"),), "blocked"),
        ((PlanStep("failed", "Failed.", status="failed"),), "blocked"),
        (
            (
                PlanStep("blocked", "Blocked.", status="blocked"),
                PlanStep("pending", "Pending.", status="pending"),
            ),
            "in-progress",
        ),
    ],
)
def test_running_plan_status_mapping(
    steps: tuple[PlanStep, ...],
    expected: str,
) -> None:
    projection = project(plan(steps=steps))[0]

    assert dict(projection.variants[0].element.attributes) == {"status": expected}


def test_draft_plan_and_every_step_status_use_context_protocol_values() -> None:
    step_statuses: tuple[PlanStepStatus, ...] = (
        "pending",
        "assigned",
        "running",
        "blocked",
        "failed",
        "completed",
        "cancelled",
    )
    steps = tuple(
        PlanStep(step_status, f"{step_status} instruction.", status=step_status)
        for step_status in step_statuses
    )

    full = project(plan(status="draft", steps=steps))[0].variants[0].element

    assert dict(full.attributes) == {"status": "pending"}
    assert [dict(step.attributes)["status"] for step in full.children[1:]] == [
        "pending",
        "in-progress",
        "in-progress",
        "blocked",
        "blocked",
        "completed",
        "cancelled",
    ]


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_plan_is_not_projected(status: PlanStatus) -> None:
    assert project(plan(status=status)) == ()


@pytest.mark.parametrize(
    "steps",
    [
        (),
        (PlanStep("completed", "Done.", status="completed"),),
        (PlanStep("cancelled", "Cancelled.", status="cancelled"),),
        (
            PlanStep("completed", "Done.", status="completed"),
            PlanStep("cancelled", "Cancelled.", status="cancelled"),
        ),
    ],
)
def test_inconsistent_running_plan_fails_without_partial_projection(
    steps: tuple[PlanStep, ...],
) -> None:
    with pytest.raises(
        PlanProjectionError,
        match="^invalid active plan state$",
    ):
        project(plan(steps=steps))


def test_projection_is_safe_minimal_and_deterministically_trimmable() -> None:
    current = PlanState(
        plan_id="plan_1",
        objective='Review <SDK> & "ship".',
        owner_agent_id="owner_secret",
        status="running",
        steps=(
            PlanStep(
                'done_1<&"',
                'Completed <one> & "verified".',
                status="completed",
                required_capabilities=("capability_secret",),
                assigned_agent_id="worker_secret",
                template_id="template_secret",
                task_id="task_secret",
                depends_on=("dependency_secret",),
                evidence_ids=("evidence_secret",),
                attempts=2,
                last_failed_at=10,
                next_retry_at=20,
                retry_status="scheduled",
            ),
            PlanStep("pending", "Pending work.", status="pending"),
            PlanStep("done_2", "Completed two.", status="completed"),
            PlanStep("assigned", "Assigned work.", status="assigned"),
            PlanStep("cancelled", "Cancelled work.", status="cancelled"),
        ),
        evidence=(
            EvidenceHandle(
                "evidence_secret",
                "artifact",
                "evidence summary secret",
                uri="file:///private/evidence.txt",
                metadata={"path": "/private/evidence.txt"},
            ),
        ),
        assignments=(
            PlanAssignment(
                "plan_1",
                'done_1<&"',
                "template_secret",
                "task_secret",
                "worker_secret",
                99,
            ),
        ),
        created_at=1,
        updated_at=2,
        workspace=WorkspaceHandle("workspace_secret", "session", "/private"),
    )

    projection = project(current)[0]

    assert projection.slot == "active-plan"
    assert projection.owner == "PlannerRuntime"
    assert [variant.omitted_count for variant in projection.variants] == [0, 1, 2]
    assert [
        [dict(child.attributes).get("handle") for child in variant.element.children[1:]]
        for variant in projection.variants
    ] == [
        ['done_1<&"', "pending", "done_2", "assigned", "cancelled"],
        ["pending", "done_2", "assigned", "cancelled"],
        ["pending", "assigned", "cancelled"],
    ]
    assert all(
        set(dict(step.attributes)) == {"handle", "status"}
        for step in projection.variants[0].element.children[1:]
    )

    snapshot = ContextSnapshotRenderer(RecordingTokenCounter(1)).render((projection,))
    assert 'Review &lt;SDK&gt; &amp; "ship".' in snapshot.xml
    assert 'handle="done_1&lt;&amp;&quot;"' in snapshot.xml
    assert "Completed &lt;one&gt; &amp; \"verified\"." in snapshot.xml
    for hidden in (
        "owner_secret",
        "capability_secret",
        "worker_secret",
        "template_secret",
        "task_secret",
        "dependency_secret",
        "evidence_secret",
        "evidence summary secret",
        "file://",
        "/private",
        "workspace_secret",
        "retry_status",
        "last_failed_at",
        "next_retry_at",
        "created_at",
        "updated_at",
    ):
        assert hidden not in snapshot.xml
