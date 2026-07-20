from __future__ import annotations

from typing import Protocol, cast

from agentos.planning.dispatch import PlanDispatchReport
from agentos.planning.errors import PlanClaimLostError
from agentos.planning.models import (
    PlanRetryPolicy,
    PlanState,
    PlanStatus,
    PlanStep,
)
from agentos.planning.scheduling_reports import (
    PlanClaimedSchedulerTickReport,
    PlanClaimedSchedulerTickSkip,
    PlanClaimedSchedulerTickSkipReason,
    PlanClaimSweepReport,
    PlanClaimSweepSkip,
    PlanClaimSweepSkipReason,
    PlanSchedulerRetryReset,
    PlanSchedulerTickReport,
    PlannerSchedulablePlan,
    PlannerSchedulablePlanReason,
)
from agentos.planning.store import (
    PlanClaimRecord,
    PlanClaimResult,
    PlanClaimStore,
    PlanClaimSweepStore,
    PlanStore,
)
class _SchedulingRuntime(Protocol):
    store: PlanStore
    claim_store: PlanClaimStore | None
    retry_policy: PlanRetryPolicy

    def _clock(self) -> float: ...

    async def ready_steps(self, plan_id: str) -> tuple[PlanStep, ...]: ...

    async def retryable_steps(self, plan_id: str) -> tuple[PlanStep, ...]: ...

    async def retry_step(self, plan_id: str, step_id: str) -> PlanState: ...

    async def dispatch_ready_steps(
        self,
        plan_id: str,
        *,
        default_template_id: str | None = None,
        limit: int | None = None,
    ) -> PlanDispatchReport: ...

    def _pending_dispatch_step_ids(self, plan: PlanState) -> tuple[str, ...]: ...

    def _validate_plan_statuses(
        self,
        statuses: tuple[PlanStatus, ...],
    ) -> tuple[PlanStatus, ...]: ...

    async def _run_scheduler_tick_with_claim(
        self,
        claim: PlanClaimRecord | None,
        *,
        default_template_id: str | None,
        retry_limit: int | None,
        dispatch_limit: int | None,
    ) -> PlanSchedulerTickReport: ...


async def schedulable_plans(
    runtime: _SchedulingRuntime,
    *,
    owner_agent_id: str | None = None,
    statuses: tuple[PlanStatus, ...] = ("draft", "running"),
    limit: int | None = None,
) -> tuple[PlannerSchedulablePlan, ...]:
    """筛选包含就绪、到期重试或待恢复派发工作的 Plan。"""

    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    allowed_statuses = runtime._validate_plan_statuses(statuses)
    selection_now = float(runtime._clock())
    summaries: list[PlannerSchedulablePlan] = []
    for plan in await runtime.store.list_plans(owner_agent_id):
        if plan.status not in allowed_statuses:
            continue
        ready_step_ids = tuple(
            step.step_id for step in _ready_steps(plan)
        )
        retryable_step_ids = tuple(
            step.step_id
            for step in _retryable_steps(
                plan,
                retry_policy=runtime.retry_policy,
                now=selection_now,
            )
        )
        pending_dispatch_step_ids = runtime._pending_dispatch_step_ids(plan)
        reasons: list[PlannerSchedulablePlanReason] = []
        if ready_step_ids:
            reasons.append("ready-steps")
        if retryable_step_ids:
            reasons.append("due-retries")
        if pending_dispatch_step_ids:
            reasons.append("pending-dispatch")
        if not reasons:
            continue
        summaries.append(
            PlannerSchedulablePlan(
                plan_id=plan.plan_id,
                owner_agent_id=plan.owner_agent_id,
                status=plan.status,
                ready_step_ids=ready_step_ids,
                retryable_step_ids=retryable_step_ids,
                reasons=tuple(reasons),
                updated_at=plan.updated_at,
            ),
        )
        if limit is not None and len(summaries) >= limit:
            break
    return tuple(summaries)


async def claim_schedulable_plans(
    runtime: _SchedulingRuntime,
    *,
    worker_id: str,
    lease_seconds: float,
    owner_agent_id: str | None = None,
    statuses: tuple[PlanStatus, ...] = ("draft", "running"),
    limit: int | None = None,
) -> tuple[PlanClaimResult, ...]:
    """通过配置的 Claim Store 领取可调度 Plan。"""

    claim_store = _require_claim_store(runtime)
    if not worker_id.strip():
        raise ValueError("worker_id must not be empty")
    lease_seconds = float(lease_seconds)
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be > 0")
    summaries = await schedulable_plans(
        runtime,
        owner_agent_id=owner_agent_id,
        statuses=statuses,
        limit=limit,
    )
    claims = []
    for summary in summaries:
        claims.append(
            await claim_store.claim_plan(
            plan_id=summary.plan_id,
            owner_agent_id=summary.owner_agent_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=float(runtime._clock()),
            )
        )
    return tuple(claims)


async def sweep_expired_claims(
    runtime: _SchedulingRuntime,
    *,
    owner_agent_id: str | None = None,
    now: float | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> PlanClaimSweepReport:
    """报告并可选释放过期的 Plan Claim。"""

    claim_store = _require_claim_store(runtime)
    if not hasattr(claim_store, "expired_claims") or not hasattr(
        claim_store,
        "release_expired_claim",
    ):
        raise RuntimeError("claim_store must implement PlanClaimSweepStore")
    if owner_agent_id is not None and not owner_agent_id.strip():
        raise ValueError("owner_agent_id must not be empty")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    now_value = float(runtime._clock() if now is None else now)
    sweep_store = cast(PlanClaimSweepStore, claim_store)
    checked = await sweep_store.expired_claims(
        now=now_value,
        owner_agent_id=owner_agent_id,
        limit=limit,
    )
    released: list[PlanClaimRecord] = []
    skipped: list[PlanClaimSweepSkip] = []
    if not dry_run:
        for claim in checked:
            if await sweep_store.release_expired_claim(claim, now=now_value):
                released.append(claim)
            else:
                skipped.append(
                    PlanClaimSweepSkip(
                        plan_id=claim.plan_id,
                        reason="release-race",
                        detail="claim changed before stale release",
                    ),
                )
    return PlanClaimSweepReport(
        checked_claims=checked,
        released_claims=tuple(released),
        skipped_claims=tuple(skipped),
        dry_run=dry_run,
        now=now_value,
        owner_agent_id=owner_agent_id,
    )


async def scheduler_tick(
    runtime: _SchedulingRuntime,
    plan_id: str,
    *,
    default_template_id: str | None = None,
    retry_limit: int | None = None,
    dispatch_limit: int | None = None,
) -> PlanSchedulerTickReport:
    """先重置到期重试，再派发依赖已满足的 Step。"""

    if retry_limit is not None and retry_limit < 1:
        raise ValueError("retry_limit must be >= 1")
    if dispatch_limit is not None and dispatch_limit < 1:
        raise ValueError("dispatch_limit must be >= 1")
    retry_resets: list[PlanSchedulerRetryReset] = []
    for step in await runtime.retryable_steps(plan_id):
        if retry_limit is not None and len(retry_resets) >= retry_limit:
            break
        await runtime.retry_step(plan_id, step.step_id)
        retry_resets.append(
            PlanSchedulerRetryReset(
                plan_id=plan_id,
                step_id=step.step_id,
                attempts=step.attempts,
            ),
        )
    dispatch = await runtime.dispatch_ready_steps(
        plan_id,
        default_template_id=default_template_id,
        limit=dispatch_limit,
    )
    return PlanSchedulerTickReport(
        plan_id=plan_id,
        retry_resets=tuple(retry_resets),
        dispatch=dispatch,
    )


async def claimed_scheduler_tick(
    runtime: _SchedulingRuntime,
    *,
    worker_id: str,
    lease_seconds: float,
    owner_agent_id: str | None = None,
    statuses: tuple[PlanStatus, ...] = ("draft", "running"),
    limit: int | None = None,
    default_template_id: str | None = None,
    retry_limit: int | None = None,
    dispatch_limit: int | None = None,
    release_after_tick: bool = False,
) -> PlanClaimedSchedulerTickReport:
    """领取可调度 Plan，并在 Claim fencing 内执行有界 Tick。"""

    claim_store = _require_claim_store(runtime)
    if not worker_id.strip():
        raise ValueError("worker_id must not be empty")
    lease_seconds = float(lease_seconds)
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be > 0")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    if retry_limit is not None and retry_limit < 1:
        raise ValueError("retry_limit must be >= 1")
    if dispatch_limit is not None and dispatch_limit < 1:
        raise ValueError("dispatch_limit must be >= 1")
    statuses = runtime._validate_plan_statuses(statuses)

    claims: list[PlanClaimResult] = []
    tick_reports: list[PlanSchedulerTickReport] = []
    skipped: list[PlanClaimedSchedulerTickSkip] = []
    released_plan_ids: list[str] = []
    for summary in await schedulable_plans(
        runtime,
        owner_agent_id=owner_agent_id,
        statuses=statuses,
        limit=limit,
    ):
        claim_result = await claim_store.claim_plan(
            plan_id=summary.plan_id,
            owner_agent_id=summary.owner_agent_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=float(runtime._clock()),
        )
        claims.append(claim_result)
        if claim_result.status != "claimed":
            skipped.append(
                PlanClaimedSchedulerTickSkip(
                    plan_id=summary.plan_id,
                    reason="busy",
                    detail="plan is leased by another scheduler worker",
                    claim_result=claim_result,
                ),
            )
            continue
        try:
            tick_reports.append(
                await runtime._run_scheduler_tick_with_claim(
                    claim_result.claim,
                    default_template_id=default_template_id,
                    retry_limit=retry_limit,
                    dispatch_limit=dispatch_limit,
                ),
            )
        except PlanClaimLostError as error:
            skipped.append(
                PlanClaimedSchedulerTickSkip(
                    plan_id=summary.plan_id,
                    reason="claim-lost",
                    detail=str(error) or "claim changed before scheduler save",
                    claim_result=claim_result,
                ),
            )
        except Exception as error:
            skipped.append(
                PlanClaimedSchedulerTickSkip(
                    plan_id=summary.plan_id,
                    reason="tick-failed",
                    detail=str(error) or error.__class__.__name__,
                    claim_result=claim_result,
                ),
            )
        finally:
            if release_after_tick:
                released = await claim_store.release_plan(
                    plan_id=summary.plan_id,
                    worker_id=worker_id,
                    owner_agent_id=summary.owner_agent_id,
                )
                if released:
                    released_plan_ids.append(summary.plan_id)

    return PlanClaimedSchedulerTickReport(
        worker_id=worker_id,
        claims=tuple(claims),
        tick_reports=tuple(tick_reports),
        skipped=tuple(skipped),
        released_plan_ids=tuple(released_plan_ids),
    )


def pending_dispatch_step_ids(plan: PlanState) -> tuple[str, ...]:
    """返回状态仍与 pending Assignment 一致的 Step ID。"""

    step_by_id = {step.step_id: step for step in plan.steps}
    pending_step_ids: list[str] = []
    for assignment in plan.assignments:
        if assignment.dispatch_status != "pending":
            continue
        step = step_by_id.get(assignment.step_id)
        if step is None:
            continue
        if (
            step.status != "assigned"
            or step.task_id != assignment.task_id
            or step.assigned_agent_id != assignment.target_agent_id
        ):
            continue
        pending_step_ids.append(assignment.step_id)
    return tuple(dict.fromkeys(pending_step_ids))


def _ready_steps(plan: PlanState) -> tuple[PlanStep, ...]:
    completed = {
        step.step_id
        for step in plan.steps
        if step.status == "completed"
    }
    return tuple(
        step
        for step in plan.steps
        if step.status == "pending"
        and set(step.depends_on).issubset(completed)
    )


def _retryable_steps(
    plan: PlanState,
    *,
    retry_policy: PlanRetryPolicy,
    now: float,
) -> tuple[PlanStep, ...]:
    completed = {
        step.step_id
        for step in plan.steps
        if step.status == "completed"
    }
    return tuple(
        step
        for step in plan.steps
        if step.status == "failed"
        and step.retry_status == "scheduled"
        and step.next_retry_at is not None
        and step.next_retry_at <= now
        and step.attempts < retry_policy.max_attempts
        and set(step.depends_on).issubset(completed)
    )


def _require_claim_store(runtime: _SchedulingRuntime) -> PlanClaimStore:
    if runtime.claim_store is None:
        raise RuntimeError("claim_store is required to claim schedulable plans")
    return runtime.claim_store


__all__ = [
    "PlanClaimedSchedulerTickReport",
    "PlanClaimedSchedulerTickSkip",
    "PlanClaimedSchedulerTickSkipReason",
    "PlanClaimSweepReport",
    "PlanClaimSweepSkip",
    "PlanClaimSweepSkipReason",
    "PlanSchedulerRetryReset",
    "PlanSchedulerTickReport",
    "PlannerSchedulablePlan",
    "PlannerSchedulablePlanReason",
]
