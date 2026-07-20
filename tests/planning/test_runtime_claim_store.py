from __future__ import annotations


import pytest

from agentos.planning import (
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanState,
)
from tests.planning._async import async_test


@async_test
async def test_in_memory_plan_claim_store_claims_releases_and_expires_leases() -> None:
    store = InMemoryPlanClaimStore()

    first = await store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_a",
        lease_seconds=30.0,
        now=10.0,
    )
    busy = await store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_b",
        lease_seconds=30.0,
        now=20.0,
    )
    renewed = await store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_a",
        lease_seconds=30.0,
        now=25.0,
    )

    assert first.as_dict() == {
        "status": "claimed",
        "claim": {
            "plan_id": "plan_1",
            "owner_agent_id": "leader",
            "worker_id": "worker_a",
            "claimed_at": 10.0,
            "lease_expires_at": 40.0,
            "generation": 1,
        },
        "existing_claim": None,
    }
    assert busy.status == "busy"
    assert busy.claim is None
    assert busy.existing_claim is not None
    assert busy.existing_claim.worker_id == "worker_a"
    assert renewed.status == "claimed"
    assert renewed.claim is not None
    assert renewed.claim.worker_id == "worker_a"
    assert renewed.claim.claimed_at == 25.0
    assert renewed.claim.lease_expires_at == 55.0
    assert renewed.claim.generation == 2
    assert await store.release_plan(plan_id="plan_1", worker_id="worker_b") is False
    assert await store.release_plan(plan_id="plan_1", worker_id="worker_a") is True
    assert await store.get_claim("plan_1") is None

    expired = await store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_a",
        lease_seconds=5.0,
        now=100.0,
    )
    takeover = await store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_b",
        lease_seconds=5.0,
        now=106.0,
    )

    assert expired.status == "claimed"
    assert takeover.status == "claimed"
    assert takeover.claim is not None
    assert takeover.claim.worker_id == "worker_b"
    assert takeover.claim.generation == 2


@async_test
async def test_in_memory_plan_claim_store_does_not_renew_active_claim_for_other_owner() -> (
    None
):
    store = InMemoryPlanClaimStore()
    first = await store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="shared_worker",
        lease_seconds=30.0,
        now=10.0,
    )

    other_owner = await store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="other",
        worker_id="shared_worker",
        lease_seconds=30.0,
        now=20.0,
    )

    assert other_owner.status == "busy"
    assert other_owner.existing_claim == first.claim
    assert await store.get_claim("plan_1") == first.claim


@async_test
async def test_in_memory_plan_claim_store_release_can_require_matching_owner() -> None:
    store = InMemoryPlanClaimStore()
    first = await store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="shared_worker",
        lease_seconds=30.0,
        now=10.0,
    )

    assert (
        await store.release_plan(
            plan_id="plan_1",
            worker_id="shared_worker",
            owner_agent_id="other",
        )
        is False
    )
    assert await store.get_claim("plan_1") == first.claim
    assert (
        await store.release_plan(
            plan_id="plan_1",
            worker_id="shared_worker",
            owner_agent_id="leader",
        )
        is True
    )
    assert await store.get_claim("plan_1") is None


@async_test
async def test_plan_claim_store_release_expired_claim_is_generation_safe() -> None:
    claim_store = InMemoryPlanClaimStore()
    original = await claim_store.claim_plan(
        plan_id="expired_plan",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=10.0,
    )
    assert original.claim is not None
    takeover = await claim_store.claim_plan(
        plan_id="expired_plan",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=30.0,
        now=30.0,
    )
    assert takeover.claim is not None

    released = await claim_store.release_expired_claim(
        original.claim,
        now=30.0,
    )

    assert released is False
    current = await claim_store.get_claim("expired_plan")
    assert current is not None
    assert current.worker_id == "scheduler_b"
    assert current.generation == 2


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {
                "plan_id": "",
                "owner_agent_id": "leader",
                "worker_id": "worker",
                "lease_seconds": 10.0,
                "now": 1.0,
            },
            "plan_id",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": " ",
                "worker_id": "worker",
                "lease_seconds": 10.0,
                "now": 1.0,
            },
            "owner_agent_id",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": "leader",
                "worker_id": "",
                "lease_seconds": 10.0,
                "now": 1.0,
            },
            "worker_id",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": "leader",
                "worker_id": "worker",
                "lease_seconds": 0.0,
                "now": 1.0,
            },
            "lease_seconds",
        ),
    ],
)
@async_test
async def test_in_memory_plan_claim_store_rejects_invalid_claims(
    kwargs: dict[str, object],
    match: str,
) -> None:
    store = InMemoryPlanClaimStore()

    with pytest.raises(ValueError, match=match):
        await store.claim_plan(**kwargs)


@async_test
async def test_in_memory_plan_store_claim_guarded_save_requires_exact_live_claim() -> (
    None
):
    store = InMemoryPlanStore()
    claim_store = InMemoryPlanClaimStore()
    original = PlanState(
        plan_id="plan_1",
        objective="Claim-guard local plan store.",
        owner_agent_id="leader",
        status="running",
    )
    await store.create_plan(original)
    record = await store.get_plan_record("plan_1")
    assert record is not None
    claim = (
        await claim_store.claim_plan(
            plan_id="plan_1",
            owner_agent_id="leader",
            worker_id="scheduler_a",
            lease_seconds=20.0,
            now=10.0,
        )
    ).claim
    assert claim is not None

    assert (
        await store.save_plan_if_claimed(
            original.with_status("completed", now=11.0),
            claim,
            expected_revision=record.revision,
            now=11.0,
        )
        is False
    )

    store.bind_claim_store(claim_store)
    assert (
        await store.save_plan_if_claimed(
            original.with_status("completed", now=11.0),
            claim,
            expected_revision=record.revision,
            now=11.0,
        )
        is True
    )

    fresh = await store.get_plan_record("plan_1")
    assert fresh is not None
    assert fresh.plan.status == "completed"
    assert (
        await store.save_plan_if_claimed(
            fresh.plan.with_status("failed", now=31.0),
            claim,
            expected_revision=fresh.revision,
            now=31.0,
        )
        is False
    )
