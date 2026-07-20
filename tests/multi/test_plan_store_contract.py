from agentos.planning import (
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanClaimRecord,
    PlanStore,
)
from agentos.testing.contracts.plan_store import run_plan_store_contract
from tests.planning._async import async_test


@async_test
async def test_in_memory_plan_store_satisfies_reusable_plan_store_contract() -> None:
    claim_stores: dict[int, InMemoryPlanClaimStore] = {}

    def factory() -> InMemoryPlanStore:
        store = InMemoryPlanStore()
        claim_store = InMemoryPlanClaimStore()
        store.bind_claim_store(claim_store)
        claim_stores[id(store)] = claim_store
        return store

    async def seed_claim(store: PlanStore, claim: PlanClaimRecord) -> None:
        claim_store = claim_stores[id(store)]
        result = await claim_store.claim_plan(
            plan_id=claim.plan_id,
            owner_agent_id=claim.owner_agent_id,
            worker_id=claim.worker_id,
            lease_seconds=claim.lease_expires_at - claim.claimed_at,
            now=claim.claimed_at,
        )
        assert result.claim == claim

    await run_plan_store_contract(factory, seed_claim=seed_claim)
