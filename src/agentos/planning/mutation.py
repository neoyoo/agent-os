from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar

from agentos.planning.errors import PlanClaimLostError, PlanConflictError
from agentos.planning.models import PlanState
from agentos.planning.store import PlanClaimRecord, PlanClaimStore, PlanStore


class PlanMutationCoordinator:
    """统一执行 Plan CAS 保存与 Claim fencing。"""

    def __init__(
        self,
        *,
        store: PlanStore,
        claim_store: PlanClaimStore | None,
        clock: Callable[[], float],
    ) -> None:
        self._store = store
        self._claim_store = claim_store
        self._clock = clock
        self._claim_context: ContextVar[Mapping[str, PlanClaimRecord]] = ContextVar(
            f"plan_claim_context_{id(self)}",
            default={},
        )

    @asynccontextmanager
    async def claim_scope(self, claim: PlanClaimRecord) -> AsyncIterator[None]:
        """在当前 async task 内激活一个精确 Plan Claim。"""

        claims = dict(self._claim_context.get())
        claims[claim.plan_id] = claim
        token = self._claim_context.set(claims)
        try:
            yield
        finally:
            self._claim_context.reset(token)

    async def save(
        self,
        plan: PlanState,
        *,
        expected_revision: int | None = None,
    ) -> None:
        """按当前 Claim scope 选择 CAS 或 claim-guarded save。"""

        claim = self._claim_context.get().get(plan.plan_id)
        if claim is None:
            await self._save_with_revision(
                plan,
                expected_revision=expected_revision,
            )
            return
        await self._save_with_claim(
            plan,
            claim=claim,
            expected_revision=expected_revision,
        )

    async def ensure_active_claim(self, plan_id: str) -> None:
        """在外部派发副作用前重新验证当前 Claim。"""

        claim = self._claim_context.get().get(plan_id)
        if claim is None:
            return
        if self._claim_store is None:
            raise PlanClaimLostError(
                f"claim store is required to verify active claim: {plan_id}",
            )
        current = await self._claim_store.get_claim(plan_id)
        if current != claim:
            raise PlanClaimLostError(
                f"claim changed before dispatching plan assignment: {plan_id}",
            )
        if current.lease_expires_at <= float(self._clock()):
            raise PlanClaimLostError(
                f"claim expired before dispatching plan assignment: {plan_id}",
            )

    async def _save_with_revision(
        self,
        plan: PlanState,
        *,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is None:
            await self._store.save_plan(plan)
            return
        compare_save = getattr(self._store, "save_plan_if_unchanged", None)
        if not callable(compare_save):
            raise PlanConflictError(
                "PlanStore must implement save_plan_if_unchanged for "
                f"mutation safety: {plan.plan_id}",
            )
        if await compare_save(plan, expected_revision=expected_revision):
            return
        raise PlanConflictError(f"plan changed before saving: {plan.plan_id}")

    async def _save_with_claim(
        self,
        plan: PlanState,
        *,
        claim: PlanClaimRecord,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is None:
            raise PlanClaimLostError(
                "expected_revision is required for claim-guarded save: "
                f"{plan.plan_id}",
            )
        guarded_save = getattr(self._store, "save_plan_if_claimed", None)
        if not callable(guarded_save):
            raise PlanClaimLostError(
                "PlanStore must implement save_plan_if_claimed for "
                f"claim-guarded save: {plan.plan_id}",
            )
        if await guarded_save(
            plan,
            claim,
            expected_revision=expected_revision,
            now=float(self._clock()),
        ):
            return
        raise PlanClaimLostError(
            f"claim changed before saving plan: {plan.plan_id}",
        )
