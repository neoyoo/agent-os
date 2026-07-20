from __future__ import annotations

from tests.planning._async import async_test


from agentos.planning import (
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanClaimRecord,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from agentos.planning.scheduling_reports import (
    PlanClaimedSchedulerTickReport,
)
from tests.planning._runtime_fixtures import (
    FakeCoordinator,
    ManualClock,
)


@async_test
async def test_planner_runtime_claimed_scheduler_tick_skips_busy_plans() -> None:
    coordinator = FakeCoordinator()
    claim_store = InMemoryPlanClaimStore()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=coordinator,
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
            plan_id="busy_plan",
            objective="Already leased work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="busy_step",
                    instruction="Should not be dispatched.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="free_plan",
            objective="Claimable work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="free_step",
                    instruction="Should be dispatched.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    await claim_store.claim_plan(
        plan_id="busy_plan",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        now=10.0,
    )

    report = await runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=30.0,
        default_template_id="reviewer",
    )

    busy_plan = await runtime.get_plan("busy_plan")
    free_plan = await runtime.get_plan("free_plan")
    assert isinstance(report, PlanClaimedSchedulerTickReport)
    assert report.worker_id == "scheduler_b"
    assert [claim.status for claim in report.claims] == ["busy", "claimed"]
    assert [tick.plan_id for tick in report.tick_reports] == ["free_plan"]
    assert report.skipped[0].plan_id == "busy_plan"
    assert report.skipped[0].reason == "busy"
    assert report.skipped[0].claim_result.existing_claim is not None
    assert report.skipped[0].claim_result.existing_claim.worker_id == "scheduler_a"
    assert report.released_plan_ids == ()
    assert busy_plan.steps[0].status == "pending"
    assert free_plan.steps[0].status == "assigned"
    assert len(coordinator.spawn_calls) == 1


@async_test
async def test_planner_runtime_claimed_scheduler_tick_dispatches_only_after_claim_guarded_save() -> (
    None
):
    class RacingClaimStore(InMemoryPlanClaimStore):
        def __init__(self) -> None:
            super().__init__()
            self._raced = False

        async def get_claim(self, plan_id: str) -> PlanClaimRecord | None:
            if plan_id == "plan_1" and not self._raced:
                self._raced = True
                clock.value = 11.0
                await self.claim_plan(
                    plan_id="plan_1",
                    owner_agent_id="leader",
                    worker_id="scheduler_b",
                    lease_seconds=30.0,
                    now=11.0,
                )
            return await super().get_claim(plan_id)

    clock = ManualClock(10.0)
    claim_store = RacingClaimStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=clock,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Race-safe scheduler work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not be saved by stale scheduler.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = await runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=1.0,
        default_template_id="reviewer",
    )

    plan = await runtime.get_plan("plan_1")
    assert [claim.status for claim in report.claims] == ["claimed"]
    assert report.tick_reports == ()
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    assert plan.steps[0].status == "assigned"
    assert plan.steps[0].task_id is not None
    assert plan.assignments[0].dispatch_status == "pending"
    assert coordinator.spawn_calls == []
    current_claim = await claim_store.get_claim("plan_1")
    assert current_claim is not None
    assert current_claim.worker_id == "scheduler_b"


@async_test
async def test_planner_runtime_claimed_scheduler_tick_dispatches_only_after_revision_guarded_save() -> (
    None
):
    class RacingCoordinator(FakeCoordinator):
        async def spawn(self, **kwargs: object) -> None:
            current = await store.get_plan("plan_1")
            assert current is not None
            await store.save_plan(current.with_status("failed", now=11.0))
            return await super().spawn(**kwargs)

    store = InMemoryPlanStore()
    claim_store = InMemoryPlanClaimStore()
    coordinator = RacingCoordinator()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=coordinator,
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
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Race-safe scheduler work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not be saved over a concurrent update.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = await runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        default_template_id="reviewer",
    )

    plan = await runtime.get_plan("plan_1")
    assert len(report.tick_reports) == 1
    assert report.skipped == ()
    assert plan.status == "failed"
    assert plan.steps[0].status == "assigned"
    assert plan.steps[0].task_id is not None
    assert coordinator.spawn_calls[0]["task_id"] == plan.steps[0].task_id
    current_claim = await claim_store.get_claim("plan_1")
    assert current_claim is not None
    assert current_claim.worker_id == "scheduler_a"


@async_test
async def test_planner_runtime_claimed_scheduler_tick_can_release_claims_after_tick() -> (
    None
):
    clock = ManualClock(10.0)
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=clock,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Claim, tick, and release.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Run first worker.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    first = await runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        default_template_id="reviewer",
        release_after_tick=True,
    )
    await runtime.store.create_plan(
        PlanState(
            plan_id="plan_2",
            objective="Later work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_2",
                    instruction="Run second worker.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    clock.value = 11.0
    second = await runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=30.0,
        default_template_id="reviewer",
        release_after_tick=True,
    )

    assert first.released_plan_ids == ("plan_1",)
    assert await claim_store.get_claim("plan_1") is None
    assert [tick.plan_id for tick in second.tick_reports] == ["plan_2"]
    assert second.claims[0].claim is not None
    assert second.claims[0].claim.worker_id == "scheduler_b"
    assert second.released_plan_ids == ("plan_2",)
    assert await claim_store.get_claim("plan_2") is None
