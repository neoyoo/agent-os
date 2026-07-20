from __future__ import annotations

import asyncio

from agentos.planning import (
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanClaimRecord,
    PlanDispatchReport,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from agentos.planning.scheduling_reports import (
    PlanSchedulerTickReport,
)
from tests.planning._runtime_fixtures import (
    FakeCoordinator,
    ManualClock,
)
from tests.planning._async import async_test


@async_test
async def test_planner_runtime_claimed_scheduler_tick_stops_when_claim_changes_before_save() -> (
    None
):
    class RacingPlanStore(InMemoryPlanStore):
        async def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            clock.value = 11.0
            await claim_store.claim_plan(
                plan_id=plan.plan_id,
                owner_agent_id=plan.owner_agent_id,
                worker_id="scheduler_b",
                lease_seconds=30.0,
                now=11.0,
            )
            return await super().save_plan_if_claimed(
                plan,
                claim,
                expected_revision=expected_revision,
                now=11.0,
            )

    clock = ManualClock(10.0)
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    store = RacingPlanStore()
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
        clock=clock,
    )
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Claim-guard local scheduler mutation.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not save after claim is lost.",
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
    assert plan.steps[0].status == "pending"
    assert coordinator.spawn_calls == []
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    current_claim = await claim_store.get_claim("plan_1")
    assert current_claim is not None
    assert current_claim.worker_id == "scheduler_b"


@async_test
async def test_planner_runtime_claimed_scheduler_requires_atomic_claim_guarded_save() -> (
    None
):
    class CompareOnlyPlanStore(InMemoryPlanStore):
        save_plan_if_claimed = None  # type: ignore[assignment]

    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    store = CompareOnlyPlanStore()
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
            objective="Require atomic claim-guarded scheduler saves.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not dispatch through CAS fallback.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = await runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=20.0,
        default_template_id="reviewer",
    )

    plan = await runtime.get_plan("plan_1")
    assert plan.steps[0].status == "pending"
    assert coordinator.spawn_calls == []
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]


@async_test
async def test_planner_runtime_claimed_scheduler_stops_when_claim_expires_before_dispatch() -> (
    None
):
    class ExpiringAfterSavePlanStore(InMemoryPlanStore):
        async def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            saved = await super().save_plan_if_claimed(
                plan,
                claim,
                expected_revision=expected_revision,
                now=now,
            )
            if saved:
                clock.value = 12.0
            return saved

    clock = ManualClock(10.0)
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    store = ExpiringAfterSavePlanStore()
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
        clock=clock,
    )
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Do not dispatch after claim expiry.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not dispatch after lease expiry.",
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
    assert plan.assignments[0].dispatch_status == "pending"
    assert coordinator.spawn_calls == []


@async_test
async def test_planner_runtime_claim_context_is_isolated_between_tasks() -> None:
    class RecordingPlanStore(InMemoryPlanStore):
        def __init__(self) -> None:
            super().__init__()
            self.claims_by_task: dict[str, PlanClaimRecord] = {}

        async def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            task = asyncio.current_task()
            assert task is not None
            self.claims_by_task[task.get_name()] = claim
            return True

        async def save_plan_if_unchanged(
            self,
            plan: PlanState,
            *,
            expected_revision: int,
        ) -> bool:
            raise AssertionError("claimed scheduler save fell back to CAS")

    class CoordinatedPlannerRuntime(PlannerRuntime):
        async def scheduler_tick(
            self,
            plan_id: str,
            *,
            default_template_id: str | None = None,
            retry_limit: int | None = None,
            dispatch_limit: int | None = None,
        ) -> PlanSchedulerTickReport:
            task = asyncio.current_task()
            assert task is not None
            if task.get_name() == "scheduler-a":
                task_a_context_ready.set()
                await task_b_saved.wait()
            else:
                await task_a_context_ready.wait()
            record = await self._require_plan_record(plan_id)
            await self._save_plan(
                record.plan.with_status("running", now=10.0),
                expected_revision=record.revision,
            )
            if task.get_name() == "scheduler-b":
                task_b_saved.set()
                await task_a_saved.wait()
            else:
                task_a_saved.set()
            return PlanSchedulerTickReport(
                plan_id=plan_id,
                retry_resets=(),
                dispatch=PlanDispatchReport(plan_id=plan_id),
            )

    store = RecordingPlanStore()
    runtime = CoordinatedPlannerRuntime(store=store, clock=lambda: 10.0)
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Keep overlapping scheduler claims isolated.",
            owner_agent_id="leader",
            status="running",
        ),
    )
    claim_a = PlanClaimRecord(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        claimed_at=1.0,
        lease_expires_at=31.0,
        generation=1,
    )
    claim_b = PlanClaimRecord(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        claimed_at=2.0,
        lease_expires_at=32.0,
        generation=2,
    )
    task_a_context_ready = asyncio.Event()
    task_b_saved = asyncio.Event()
    task_a_saved = asyncio.Event()
    task_a = asyncio.create_task(
        runtime._run_scheduler_tick_with_claim(
            claim_a,
            default_template_id=None,
            retry_limit=None,
            dispatch_limit=None,
        ),
        name="scheduler-a",
    )
    task_b = asyncio.create_task(
        runtime._run_scheduler_tick_with_claim(
            claim_b,
            default_template_id=None,
            retry_limit=None,
            dispatch_limit=None,
        ),
        name="scheduler-b",
    )

    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)

    assert store.claims_by_task["scheduler-a"] == claim_a
    assert store.claims_by_task["scheduler-b"] == claim_b
