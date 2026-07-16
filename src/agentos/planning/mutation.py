from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import local
from typing import cast

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
        self._claim_context = local()

    @contextmanager
    def claim_scope(self, claim: PlanClaimRecord) -> Iterator[None]:
        """在当前线程内为一个 Plan 激活精确 Claim。"""

        claims = self._active_claims()
        existing = claims.get(claim.plan_id)
        claims[claim.plan_id] = claim
        try:
            yield
        finally:
            if existing is None:
                claims.pop(claim.plan_id, None)
            else:
                claims[claim.plan_id] = existing

    def save(
        self,
        plan: PlanState,
        *,
        expected_revision: int | None = None,
    ) -> None:
        """按当前 Claim scope 选择 CAS 或 claim-guarded save。"""

        claim = self._active_claims().get(plan.plan_id)
        if claim is None:
            self._save_with_revision(plan, expected_revision=expected_revision)
            return
        self._save_with_claim(
            plan,
            claim=claim,
            expected_revision=expected_revision,
        )

    def ensure_active_claim(self, plan_id: str) -> None:
        """在产生外部派发副作用前重新验证 Claim。"""

        claim = self._active_claims().get(plan_id)
        if claim is None:
            return
        if self._claim_store is None:
            raise PlanClaimLostError(
                f"claim store is required to verify active claim: {plan_id}",
            )
        current = self._claim_store.get_claim(plan_id)
        if current != claim:
            raise PlanClaimLostError(
                f"claim changed before dispatching plan assignment: {plan_id}",
            )
        if current.lease_expires_at <= float(self._clock()):
            raise PlanClaimLostError(
                f"claim expired before dispatching plan assignment: {plan_id}",
            )

    def _save_with_revision(
        self,
        plan: PlanState,
        *,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is None:
            self._store.save_plan(plan)
            return
        compare_save = getattr(self._store, "save_plan_if_unchanged", None)
        if not callable(compare_save):
            raise PlanConflictError(
                "PlanStore must implement save_plan_if_unchanged for "
                f"mutation safety: {plan.plan_id}",
            )
        if compare_save(plan, expected_revision=expected_revision):
            return
        raise PlanConflictError(f"plan changed before saving: {plan.plan_id}")

    def _save_with_claim(
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
        if guarded_save(
            plan,
            claim,
            expected_revision=expected_revision,
            now=float(self._clock()),
        ):
            return
        raise PlanClaimLostError(
            f"claim changed before saving plan: {plan.plan_id}",
        )

    def _active_claims(self) -> dict[str, PlanClaimRecord]:
        claims = getattr(self._claim_context, "claims", None)
        if claims is None:
            claims = {}
            self._claim_context.claims = claims
        return cast(dict[str, PlanClaimRecord], claims)
