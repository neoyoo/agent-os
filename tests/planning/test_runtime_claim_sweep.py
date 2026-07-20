from __future__ import annotations

from tests.planning._async import async_test


import pytest

from agentos.planning import (
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlannerRuntime,
)


@async_test
async def test_planner_runtime_sweep_expired_claims_supports_dry_run_and_release() -> (
    None
):
    claim_store = InMemoryPlanClaimStore()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=claim_store,
        clock=lambda: 30.0,
    )
    await claim_store.claim_plan(
        plan_id="expired_plan",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=10.0,
    )
    await claim_store.claim_plan(
        plan_id="active_plan",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=40.0,
        now=10.0,
    )
    await claim_store.claim_plan(
        plan_id="other_owner",
        owner_agent_id="other",
        worker_id="scheduler_c",
        lease_seconds=10.0,
        now=10.0,
    )

    dry_run = await runtime.sweep_expired_claims(
        owner_agent_id="leader",
        dry_run=True,
    )
    release = await runtime.sweep_expired_claims(owner_agent_id="leader")

    assert dry_run.as_dict() == {
        "now": 30.0,
        "owner_agent_id": "leader",
        "dry_run": True,
        "checked_claims": [
            {
                "plan_id": "expired_plan",
                "owner_agent_id": "leader",
                "worker_id": "scheduler_a",
                "claimed_at": 10.0,
                "lease_expires_at": 20.0,
                "generation": 1,
            },
        ],
        "released_claims": [],
        "skipped_claims": [],
    }
    assert await claim_store.get_claim("expired_plan") is None
    assert await claim_store.get_claim("active_plan") is not None
    assert await claim_store.get_claim("other_owner") is not None
    assert release.dry_run is False
    assert [claim.plan_id for claim in release.checked_claims] == ["expired_plan"]
    assert [claim.plan_id for claim in release.released_claims] == ["expired_plan"]
    assert release.skipped_claims == ()


@async_test
async def test_planner_runtime_sweep_expired_claims_rejects_invalid_arguments() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
    )

    with pytest.raises(ValueError, match="owner_agent_id"):
        await runtime.sweep_expired_claims(owner_agent_id=" ")
    with pytest.raises(ValueError, match="limit"):
        await runtime.sweep_expired_claims(limit=0)


@async_test
async def test_planner_runtime_sweep_expired_claims_requires_sweep_store() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    with pytest.raises(RuntimeError, match="claim_store"):
        await runtime.sweep_expired_claims()
