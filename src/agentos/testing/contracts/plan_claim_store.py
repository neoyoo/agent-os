from __future__ import annotations

from collections.abc import Callable

from agentos.planning import PlanClaimRecord, PlanClaimStore
from agentos.testing.contracts._checks import (
    check_equal,
    check_in,
    check_is,
    require_not_none,
)


def run_plan_claim_store_contract(
    factory: Callable[[], PlanClaimStore],
) -> None:
    _assert_claim_busy_renew_and_takeover(factory)
    _assert_release_fences(factory)
    _assert_expired_claim_sweep(factory)
    _assert_input_validation(factory)


def _assert_claim_busy_renew_and_takeover(
    factory: Callable[[], PlanClaimStore],
) -> None:
    store = factory()
    first = store.claim_plan(
        plan_id="contract_plan_claim",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=5.0,
    )
    busy = store.claim_plan(
        plan_id="contract_plan_claim",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=10.0,
        now=6.0,
    )
    renewed = store.claim_plan(
        plan_id="contract_plan_claim",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=20.0,
        now=7.0,
    )
    other_owner = store.claim_plan(
        plan_id="contract_plan_claim",
        owner_agent_id="other_leader",
        worker_id="scheduler_a",
        lease_seconds=20.0,
        now=8.0,
    )
    takeover = store.claim_plan(
        plan_id="contract_plan_claim",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=10.0,
        now=30.0,
    )

    check_equal(first.status, "claimed", "first claim must be accepted")
    check_equal(
        first.claim,
        PlanClaimRecord(
            plan_id="contract_plan_claim",
            owner_agent_id="leader",
            worker_id="scheduler_a",
            claimed_at=5.0,
            lease_expires_at=15.0,
            generation=1,
        ),
        "first claim must record owner, worker, lease, and generation",
    )
    check_equal(busy.status, "busy", "different worker must see busy claim")
    check_equal(busy.existing_claim, first.claim, "busy result must expose claim")
    check_equal(renewed.status, "claimed", "same worker must refresh claim")
    renewed_claim = require_not_none(renewed.claim, "renewed result must include claim")
    check_equal(renewed_claim.worker_id, "scheduler_a", "renewal must keep worker")
    check_equal(renewed_claim.lease_expires_at, 27.0, "renewal must extend lease")
    check_equal(renewed_claim.generation, 2, "renewal must increment generation")
    check_equal(other_owner.status, "busy", "other owner must not take active claim")
    check_equal(
        other_owner.existing_claim,
        renewed.claim,
        "other owner busy result must expose active claim",
    )
    check_equal(takeover.status, "claimed", "expired claim must be claimable")
    takeover_claim = require_not_none(takeover.claim, "takeover must include claim")
    check_equal(takeover_claim.worker_id, "scheduler_b", "takeover must record worker")
    check_equal(takeover_claim.generation, 3, "takeover must increment generation")
    check_equal(
        store.get_claim("contract_plan_claim"),
        takeover.claim,
        "get_claim must return latest claim",
    )


def _assert_release_fences(factory: Callable[[], PlanClaimStore]) -> None:
    store = factory()
    claim = store.claim_plan(
        plan_id="contract_plan_release",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        now=10.0,
    ).claim
    claim = require_not_none(claim, "release test setup claim must succeed")

    check_is(
        store.release_plan(
            plan_id="contract_plan_release",
            worker_id="scheduler_b",
        ),
        False,
        "release_plan must reject wrong worker",
    )
    check_equal(
        store.get_claim("contract_plan_release"),
        claim,
        "wrong-worker release must leave claim intact",
    )
    check_is(
        store.release_plan(
            plan_id="contract_plan_release",
            worker_id="scheduler_a",
            owner_agent_id="other_leader",
        ),
        False,
        "release_plan must reject wrong owner",
    )
    check_equal(
        store.get_claim("contract_plan_release"),
        claim,
        "wrong-owner release must leave claim intact",
    )
    check_is(
        store.release_plan(
            plan_id="contract_plan_release",
            worker_id="scheduler_a",
            owner_agent_id="leader",
        ),
        True,
        "release_plan must accept matching worker and owner",
    )
    check_is(
        store.get_claim("contract_plan_release"),
        None,
        "successful release must remove claim",
    )


def _assert_expired_claim_sweep(factory: Callable[[], PlanClaimStore]) -> None:
    store = factory()
    sweep_store = store
    if not _looks_like_plan_claim_sweep_store(sweep_store):
        raise AssertionError(
            "PlanClaimStore contract requires expired_claims and "
            "release_expired_claim",
        )
    early = _claim(
        sweep_store,
        plan_id="contract_expired_early",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=5.0,
        now=1.0,
    )
    later = _claim(
        sweep_store,
        plan_id="contract_expired_later",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=10.0,
        now=1.0,
    )
    _claim(
        sweep_store,
        plan_id="contract_expired_other_owner",
        owner_agent_id="other_leader",
        worker_id="scheduler_c",
        lease_seconds=5.0,
        now=1.0,
    )
    _claim(
        sweep_store,
        plan_id="contract_active",
        owner_agent_id="leader",
        worker_id="scheduler_d",
        lease_seconds=100.0,
        now=1.0,
    )

    check_equal(
        sweep_store.expired_claims(now=7.0, owner_agent_id="leader"),
        (early,),
        "expired_claims must filter expired claims by owner",
    )
    check_equal(
        sweep_store.expired_claims(now=12.0, owner_agent_id="leader"),
        (early, later),
        "expired_claims must return all owner-scoped expired claims",
    )
    check_equal(
        sweep_store.expired_claims(now=12.0, owner_agent_id="leader", limit=1),
        (early,),
        "expired_claims must honor limit",
    )
    _assert_value_error(
        "limit must be >= 1",
        lambda: sweep_store.expired_claims(now=12.0, limit=0),
    )

    check_is(
        sweep_store.release_expired_claim(early, now=7.0),
        True,
        "release_expired_claim must release exact expired claim",
    )
    check_is(
        sweep_store.get_claim("contract_expired_early"),
        None,
        "released expired claim must be removed",
    )

    stale = later
    renewed = sweep_store.claim_plan(
        plan_id=later.plan_id,
        owner_agent_id=later.owner_agent_id,
        worker_id=later.worker_id,
        lease_seconds=50.0,
        now=12.0,
    ).claim
    renewed = require_not_none(renewed, "renewed stale-sweep claim must exist")
    check_is(
        sweep_store.release_expired_claim(stale, now=13.0),
        False,
        "release_expired_claim must reject stale claim generation",
    )
    check_equal(
        sweep_store.get_claim(later.plan_id),
        renewed,
        "stale expired release must leave renewed claim intact",
    )
    check_is(
        sweep_store.release_expired_claim(renewed, now=13.0),
        False,
        "release_expired_claim must reject active claim",
    )
    check_equal(
        sweep_store.get_claim(later.plan_id),
        renewed,
        "active release_expired_claim attempt must leave claim intact",
    )


def _claim(
    store: PlanClaimStore,
    *,
    plan_id: str,
    owner_agent_id: str,
    worker_id: str,
    lease_seconds: float,
    now: float,
) -> PlanClaimRecord:
    claim = store.claim_plan(
        plan_id=plan_id,
        owner_agent_id=owner_agent_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=now,
    ).claim
    return require_not_none(claim, "claim_plan test setup must return claim")


def _assert_input_validation(factory: Callable[[], PlanClaimStore]) -> None:
    store = factory()
    _assert_value_error(
        "plan_id must not be empty",
        lambda: store.claim_plan(
            plan_id="",
            owner_agent_id="leader",
            worker_id="scheduler",
            lease_seconds=1.0,
            now=1.0,
        ),
    )
    _assert_value_error(
        "owner_agent_id must not be empty",
        lambda: store.claim_plan(
            plan_id="contract_plan",
            owner_agent_id=" ",
            worker_id="scheduler",
            lease_seconds=1.0,
            now=1.0,
        ),
    )
    _assert_value_error(
        "worker_id must not be empty",
        lambda: store.claim_plan(
            plan_id="contract_plan",
            owner_agent_id="leader",
            worker_id="",
            lease_seconds=1.0,
            now=1.0,
        ),
    )
    _assert_value_error(
        "lease_seconds must be > 0",
        lambda: store.claim_plan(
            plan_id="contract_plan",
            owner_agent_id="leader",
            worker_id="scheduler",
            lease_seconds=0,
            now=1.0,
        ),
    )


def _assert_value_error(expected: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ValueError as error:
        check_in(expected, str(error), "ValueError message must describe failure")
    else:
        raise AssertionError(f"expected ValueError containing: {expected}")


def _looks_like_plan_claim_sweep_store(store: object) -> bool:
    return callable(getattr(store, "expired_claims", None)) and callable(
        getattr(store, "release_expired_claim", None),
    )


__all__ = [
    "run_plan_claim_store_contract",
]
