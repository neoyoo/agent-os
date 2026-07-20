from agentos.planning import InMemoryPlanClaimStore
from agentos.testing.contracts.plan_claim_store import run_plan_claim_store_contract
from tests.planning._async import async_test


@async_test
async def test_in_memory_plan_claim_store_satisfies_reusable_plan_claim_store_contract() -> None:
    await run_plan_claim_store_contract(lambda: InMemoryPlanClaimStore())
