from agentos.planning import InMemoryPlanStore, PlanRetryPolicy, PlanState, PlanStep
from agentos.planning.scheduling import (
    claim_schedulable_plans,
    claimed_scheduler_tick,
    pending_dispatch_step_ids,
    schedulable_plans,
)
from agentos.planning.store import PlanClaimResult
from tests.planning._async import async_test


class _Clock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class _BusyClaimStore:
    def __init__(self) -> None:
        self.claimed_at: list[float] = []

    async def claim_plan(self, **kwargs: object) -> PlanClaimResult:
        self.claimed_at.append(float(kwargs["now"]))
        return PlanClaimResult(status="busy")


class _SelectionRuntime:
    def __init__(
        self,
        *,
        store: InMemoryPlanStore,
        clock: object,
        claim_store: object | None = None,
    ) -> None:
        self.store = store
        self.claim_store = claim_store
        self.retry_policy = PlanRetryPolicy(max_attempts=3)
        self._clock = clock

    async def ready_steps(self, plan_id: str) -> tuple[PlanStep, ...]:
        raise AssertionError(f"selection re-read plan {plan_id}")

    async def retryable_steps(self, plan_id: str) -> tuple[PlanStep, ...]:
        raise AssertionError(f"selection re-read plan {plan_id}")

    def _pending_dispatch_step_ids(self, plan: PlanState) -> tuple[str, ...]:
        return pending_dispatch_step_ids(plan)

    def _validate_plan_statuses(
        self,
        statuses: tuple[str, ...],
    ) -> tuple[str, ...]:
        return statuses


def _pending_plan(plan_id: str) -> PlanState:
    return PlanState(
        plan_id=plan_id,
        objective=f"Schedule {plan_id}",
        owner_agent_id="owner",
        status="running",
        steps=(PlanStep(step_id=f"{plan_id}_step", instruction="Run."),),
    )


@async_test
async def test_schedulable_selection_uses_each_listed_plan_snapshot() -> None:
    store = InMemoryPlanStore()
    await store.create_plan(_pending_plan("plan_1"))
    runtime = _SelectionRuntime(store=store, clock=lambda: 10.0)

    summaries = await schedulable_plans(runtime)  # type: ignore[arg-type]

    assert summaries[0].ready_step_ids == ("plan_1_step",)


@async_test
async def test_claimed_tick_reads_clock_for_each_plan_claim() -> None:
    store = InMemoryPlanStore()
    await store.create_plan(_pending_plan("plan_1"))
    await store.create_plan(_pending_plan("plan_2"))
    claim_store = _BusyClaimStore()
    runtime = _SelectionRuntime(
        store=store,
        claim_store=claim_store,
        clock=_Clock(10.0, 11.0, 12.0),
    )

    report = await claimed_scheduler_tick(  # type: ignore[arg-type]
        runtime,
        worker_id="worker_1",
        lease_seconds=30.0,
    )

    assert len(report.claims) == 2
    assert claim_store.claimed_at == [11.0, 12.0]


@async_test
async def test_claim_schedulable_plans_reads_clock_for_each_lease() -> None:
    store = InMemoryPlanStore()
    await store.create_plan(_pending_plan("plan_1"))
    await store.create_plan(_pending_plan("plan_2"))
    claim_store = _BusyClaimStore()
    runtime = _SelectionRuntime(
        store=store,
        claim_store=claim_store,
        clock=_Clock(10.0, 11.0, 12.0),
    )

    claims = await claim_schedulable_plans(  # type: ignore[arg-type]
        runtime,
        worker_id="worker_1",
        lease_seconds=30.0,
    )

    assert len(claims) == 2
    assert claim_store.claimed_at == [11.0, 12.0]
