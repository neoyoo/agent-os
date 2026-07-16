from __future__ import annotations


from agentos.planning import (
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanAssignment,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from tests.planning._runtime_fixtures import (
    FakeCoordinator,
    ManualClock,
)


def test_planner_runtime_claimed_scheduler_tick_recovers_pending_assignment() -> None:
    store = InMemoryPlanStore()
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        claim_store=claim_store,
        clock=lambda: 20.0,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover pending assignment through claimed scheduler.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover through scheduler.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )

    summaries = runtime.schedulable_plans(owner_agent_id="leader")
    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
    )

    persisted = runtime.get_plan("plan_1")
    assert [summary.plan_id for summary in summaries] == ["plan_1"]
    assert summaries[0].reasons == ("pending-dispatch",)
    assert summaries[0].ready_step_ids == ()
    assert [tick.plan_id for tick in report.tick_reports] == ["plan_1"]
    assert [
        assignment.step_id for assignment in report.tick_reports[0].dispatch.assigned
    ] == [
        "step_1",
    ]
    assert coordinator.dispatch_calls[0]["task_id"] == "task_existing"
    assert persisted.assignments[0].dispatch_status == "submitted"


def test_planner_runtime_claimed_scheduler_stops_when_claim_expires_before_recovery_dispatch() -> (
    None
):
    class ExpiringDuringRecoveryStore(InMemoryPlanStore):
        def __init__(self) -> None:
            super().__init__()
            self._reads = 0

        def get_plan(self, plan_id: str) -> PlanState | None:
            self._reads += 1
            if self._reads == 3:
                clock.value = 22.0
            return super().get_plan(plan_id)

    clock = ManualClock(20.0)
    store = ExpiringDuringRecoveryStore()
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        claim_store=claim_store,
        clock=clock,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Do not recover pending dispatch after lease expiry.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recovery should keep the pending outbox item.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=1.0,
    )

    persisted = runtime.get_plan("plan_1")
    assert [claim.status for claim in report.claims] == ["claimed"]
    assert report.tick_reports == ()
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    assert coordinator.dispatch_calls == []
    assert persisted.assignments[0].dispatch_status == "pending"
