from agentos.planning import InMemoryPlanClaimStore
from agentos.testing.contracts.plan_claim_store import run_plan_claim_store_contract


def test_in_memory_plan_claim_store_satisfies_reusable_plan_claim_store_contract() -> None:
    run_plan_claim_store_contract(lambda: InMemoryPlanClaimStore())
