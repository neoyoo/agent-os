from __future__ import annotations

from collections.abc import Awaitable, Callable

from agentos.planning import (
    EvidenceHandle,
    PlanAssignment,
    PlanClaimRecord,
    PlanNotFoundError,
    PlanState,
    PlanStore,
    PlanStep,
)
from agentos.testing.contracts._checks import (
    check_equal,
    check_in,
    check_is,
    require_callable,
    require_not_none,
)
from agentos.workspace.models import WorkspaceHandle


def contract_plan(
    plan_id: str = "contract_plan",
    *,
    owner_agent_id: str = "leader",
    updated_at: float = 2.0,
) -> PlanState:
    return PlanState(
        plan_id=plan_id,
        objective="Review planner store contract.",
        owner_agent_id=owner_agent_id,
        status="running",
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="Review persisted nested plan state.",
                status="assigned",
                required_capabilities=("architecture-review",),
                assigned_agent_id="worker",
                template_id="reviewer",
                task_id="task_1",
                depends_on=("step_0",),
                evidence_ids=("evidence_1",),
                attempts=2,
                last_failed_at=2.0,
                next_retry_at=9.0,
                retry_status="scheduled",
                error="retryable failure",
            ),
        ),
        evidence=(
            EvidenceHandle(
                evidence_id="evidence_1",
                kind="text",
                summary="Planner store contract evidence.",
                uri="memory://contract/evidence",
                producer_agent_id="worker",
                metadata={"confidence": "high"},
            ),
        ),
        assignments=(
            PlanAssignment(
                plan_id=plan_id,
                step_id="step_1",
                template_id="reviewer",
                task_id="task_1",
                target_agent_id="worker",
                created_at=3.0,
                dispatch_status="submitted",
                submitted_at=4.0,
            ),
        ),
        created_at=1.0,
        updated_at=updated_at,
        workspace=WorkspaceHandle(
            workspace_id=f"task:{plan_id}",
            scope="task",
            root=f"/work/{plan_id}",
            parent_workspace_id="session:contract",
            metadata={"tenant": "contract"},
        ),
    )


async def run_plan_store_contract(
    factory: Callable[[], PlanStore],
    *,
    seed_claim: Callable[[PlanStore, PlanClaimRecord], Awaitable[None]] | None = None,
) -> None:
    await _assert_create_save_load_and_owner_filter(factory)
    await _assert_compare_and_save_contract(factory)
    await _assert_claim_guarded_save_contract(factory, seed_claim=seed_claim)


async def _assert_create_save_load_and_owner_filter(
    factory: Callable[[], PlanStore],
) -> None:
    store = factory()
    original = contract_plan()
    other = contract_plan(
        "contract_plan_other_owner",
        owner_agent_id="other_leader",
        updated_at=1.0,
    )

    await store.create_plan(original)
    await store.create_plan(other)

    check_equal(
        await store.get_plan(original.plan_id),
        original,
        "get_plan must load plan",
    )
    check_is(
        await store.get_plan("missing_plan"),
        None,
        "missing get_plan must return None",
    )
    check_equal(
        await store.list_plans("leader"),
        [original],
        "list_plans must filter by owner",
    )
    check_equal(
        await store.list_plans("other_leader"),
        [other],
        "list_plans must filter by other owner",
    )
    all_plans = await store.list_plans()
    check_equal(len(all_plans), 2, "list_plans must include all plans without owner")
    check_in(original, all_plans, "list_plans must include original plan")
    check_in(other, all_plans, "list_plans must include other owner plan")
    await _assert_value_error(
        "plan already exists",
        lambda: store.create_plan(original),
    )
    await _assert_plan_not_found(
        lambda: store.save_plan(contract_plan("missing_plan")),
    )

    updated = original.with_status("completed", now=5.0)
    await store.save_plan(updated)

    check_equal(
        await store.get_plan(original.plan_id),
        updated,
        "save_plan must update plan",
    )


async def _assert_compare_and_save_contract(
    factory: Callable[[], PlanStore],
) -> None:
    store = factory()
    original = contract_plan("contract_plan_cas")
    await store.create_plan(original)
    get_record = require_callable(
        getattr(store, "get_plan_record", None),
        "production PlanStore contract requires get_plan_record",
    )
    save_if_unchanged = require_callable(
        getattr(store, "save_plan_if_unchanged", None),
        "production PlanStore contract requires save_plan_if_unchanged",
    )

    missing_record = await get_record("missing_plan")
    check_is(missing_record, None, "missing get_plan_record must return None")
    record = require_not_none(
        await get_record(original.plan_id),
        "get_plan_record must return existing plan",
    )
    check_equal(record.plan, original, "get_plan_record must preserve plan")
    check_equal(record.revision, 0, "new plan revision must start at 0")

    updated = original.with_status("completed", now=6.0)
    check_is(
        await save_if_unchanged(updated, expected_revision=record.revision),
        True,
        "save_plan_if_unchanged must accept current revision",
    )
    fresh = require_not_none(
        await get_record(original.plan_id),
        "get_plan_record must load updated plan",
    )
    check_equal(fresh.plan, updated, "save_plan_if_unchanged must store update")
    check_equal(
        fresh.revision,
        record.revision + 1,
        "save_plan_if_unchanged must increment revision",
    )

    stale_update = updated.with_status("failed", now=7.0)
    check_is(
        await save_if_unchanged(stale_update, expected_revision=record.revision),
        False,
        "save_plan_if_unchanged must reject stale revision",
    )
    check_equal(
        await store.get_plan(original.plan_id),
        updated,
        "stale save_plan_if_unchanged must not modify plan",
    )
    await _assert_plan_not_found(
        lambda: save_if_unchanged(
            contract_plan("missing_plan"),
            expected_revision=0,
        ),
    )


async def _assert_claim_guarded_save_contract(
    factory: Callable[[], PlanStore],
    *,
    seed_claim: Callable[[PlanStore, PlanClaimRecord], Awaitable[None]] | None,
) -> None:
    store = factory()
    original = contract_plan("contract_plan_claim_guard")
    await store.create_plan(original)
    get_record = require_callable(
        getattr(store, "get_plan_record", None),
        "production PlanStore contract requires get_plan_record",
    )
    save_if_claimed = require_callable(
        getattr(store, "save_plan_if_claimed", None),
        "production PlanStore contract requires save_plan_if_claimed",
    )
    record = require_not_none(
        await get_record(original.plan_id),
        "get_plan_record must return claim-guarded plan",
    )

    claim = PlanClaimRecord(
        plan_id=original.plan_id,
        owner_agent_id="leader",
        worker_id="scheduler_a",
        claimed_at=10.0,
        lease_expires_at=40.0,
        generation=1,
    )
    if seed_claim is not None:
        await seed_claim(store, claim)

    completed = original.with_status("completed", now=11.0)
    check_is(
        await save_if_claimed(
            completed,
            claim,
            expected_revision=record.revision,
            now=11.0,
        ),
        True,
        "save_plan_if_claimed must accept current matching claim",
    )
    fresh = require_not_none(
        await get_record(original.plan_id),
        "get_plan_record must load claim-guarded update",
    )
    check_equal(fresh.plan, completed, "save_plan_if_claimed must store update")

    stale_revision_update = completed.with_status("failed", now=12.0)
    check_is(
        await save_if_claimed(
            stale_revision_update,
            claim,
            expected_revision=record.revision,
            now=12.0,
        ),
        False,
        "save_plan_if_claimed must reject stale revision",
    )
    check_equal(
        await store.get_plan(original.plan_id),
        completed,
        "stale save_plan_if_claimed must not modify plan",
    )

    wrong_worker_claim = PlanClaimRecord(
        plan_id=claim.plan_id,
        owner_agent_id=claim.owner_agent_id,
        worker_id="scheduler_b",
        claimed_at=claim.claimed_at,
        lease_expires_at=claim.lease_expires_at,
        generation=claim.generation,
    )
    check_is(
        await save_if_claimed(
            completed.with_status("failed", now=13.0),
            wrong_worker_claim,
            expected_revision=fresh.revision,
            now=13.0,
        ),
        False,
        "save_plan_if_claimed must reject wrong worker",
    )
    check_is(
        await save_if_claimed(
            completed.with_status("failed", now=41.0),
            claim,
            expected_revision=fresh.revision,
            now=41.0,
        ),
        False,
        "save_plan_if_claimed must reject expired claim",
    )
    await _assert_plan_not_found(
        lambda: save_if_claimed(
            contract_plan("missing_plan"),
            claim,
            expected_revision=0,
            now=11.0,
        ),
    )


async def _assert_value_error(
    expected: str,
    action: Callable[[], Awaitable[object]],
) -> None:
    try:
        await action()
    except ValueError as error:
        check_in(expected, str(error), "ValueError message must describe failure")
    else:
        raise AssertionError(f"expected ValueError containing: {expected}")


async def _assert_plan_not_found(
    action: Callable[[], Awaitable[object]],
) -> None:
    try:
        await action()
    except PlanNotFoundError:
        return
    else:
        raise AssertionError("expected PlanNotFoundError")


__all__ = [
    "contract_plan",
    "run_plan_store_contract",
]
