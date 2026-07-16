from __future__ import annotations

from threading import Event, Thread, current_thread


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


def test_planner_runtime_claimed_scheduler_tick_stops_when_claim_changes_before_save() -> (
    None
):
    class RacingPlanStore(InMemoryPlanStore):
        def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            clock.value = 11.0
            claim_store.claim_plan(
                plan_id=plan.plan_id,
                owner_agent_id=plan.owner_agent_id,
                worker_id="scheduler_b",
                lease_seconds=30.0,
                now=11.0,
            )
            return super().save_plan_if_claimed(
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
    store.create_plan(
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

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=1.0,
        default_template_id="reviewer",
    )

    plan = runtime.get_plan("plan_1")
    assert plan.steps[0].status == "pending"
    assert coordinator.spawn_calls == []
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    current_claim = claim_store.get_claim("plan_1")
    assert current_claim is not None
    assert current_claim.worker_id == "scheduler_b"


def test_planner_runtime_claimed_scheduler_requires_atomic_claim_guarded_save() -> None:
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
    store.create_plan(
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

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=20.0,
        default_template_id="reviewer",
    )

    plan = runtime.get_plan("plan_1")
    assert plan.steps[0].status == "pending"
    assert coordinator.spawn_calls == []
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]


def test_planner_runtime_claimed_scheduler_stops_when_claim_expires_before_dispatch() -> (
    None
):
    class ExpiringAfterSavePlanStore(InMemoryPlanStore):
        def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            saved = super().save_plan_if_claimed(
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
    store.create_plan(
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

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=1.0,
        default_template_id="reviewer",
    )

    plan = runtime.get_plan("plan_1")
    assert [claim.status for claim in report.claims] == ["claimed"]
    assert report.tick_reports == ()
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    assert plan.steps[0].status == "assigned"
    assert plan.assignments[0].dispatch_status == "pending"
    assert coordinator.spawn_calls == []


def test_planner_runtime_claim_context_is_isolated_between_threads() -> None:
    class RecordingPlanStore(InMemoryPlanStore):
        def __init__(self) -> None:
            super().__init__()
            self.claims_by_thread: dict[str, PlanClaimRecord] = {}

        def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            self.claims_by_thread[current_thread().name] = claim
            return True

        def save_plan_if_unchanged(
            self,
            plan: PlanState,
            *,
            expected_revision: int,
        ) -> bool:
            raise AssertionError("claimed scheduler save fell back to CAS")

    class CoordinatedPlannerRuntime(PlannerRuntime):
        def scheduler_tick(
            self,
            plan_id: str,
            *,
            default_template_id: str | None = None,
            retry_limit: int | None = None,
            dispatch_limit: int | None = None,
        ) -> PlanSchedulerTickReport:
            if current_thread().name == "scheduler-a":
                thread_a_context_ready.set()
                assert thread_b_saved.wait(timeout=5)
            else:
                assert thread_a_context_ready.wait(timeout=5)
            record = self._require_plan_record(plan_id)
            self._save_plan(
                record.plan.with_status("running", now=10.0),
                expected_revision=record.revision,
            )
            if current_thread().name == "scheduler-b":
                thread_b_saved.set()
                assert thread_a_saved.wait(timeout=5)
            else:
                thread_a_saved.set()
            return PlanSchedulerTickReport(
                plan_id=plan_id,
                retry_resets=(),
                dispatch=PlanDispatchReport(plan_id=plan_id),
            )

    store = RecordingPlanStore()
    runtime = CoordinatedPlannerRuntime(store=store, clock=lambda: 10.0)
    store.create_plan(
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
    thread_a_context_ready = Event()
    thread_b_saved = Event()
    thread_a_saved = Event()
    errors: list[BaseException] = []

    def run_claim(claim: PlanClaimRecord) -> None:
        try:
            runtime._run_scheduler_tick_with_claim(
                claim,
                default_template_id=None,
                retry_limit=None,
                dispatch_limit=None,
            )
        except BaseException as error:
            errors.append(error)

    thread_a = Thread(target=run_claim, args=(claim_a,), name="scheduler-a")
    thread_b = Thread(target=run_claim, args=(claim_b,), name="scheduler-b")

    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert errors == []
    assert store.claims_by_thread["scheduler-a"] == claim_a
    assert store.claims_by_thread["scheduler-b"] == claim_b
