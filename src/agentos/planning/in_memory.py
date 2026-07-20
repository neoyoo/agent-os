from __future__ import annotations

from threading import RLock

from agentos.planning.errors import PlanNotFoundError
from agentos.planning.models import PlanState
from agentos.planning.store import (
    PlanClaimRecord,
    PlanClaimResult,
    PlanStoreRecord,
)


class InMemoryPlanStore:
    """线程安全的本地 Plan Store。"""

    def __init__(self) -> None:
        self._plans: dict[str, PlanState] = {}
        self._revisions: dict[str, int] = {}
        self._claim_store: object | None = None
        self._lock = RLock()

    async def create_plan(self, plan: PlanState) -> None:
        with self._lock:
            if plan.plan_id in self._plans:
                raise ValueError(f"plan already exists: {plan.plan_id}")
            self._plans[plan.plan_id] = plan
            self._revisions[plan.plan_id] = 0

    async def save_plan(self, plan: PlanState) -> None:
        with self._lock:
            if plan.plan_id not in self._plans:
                raise PlanNotFoundError(plan.plan_id)
            self._plans[plan.plan_id] = plan
            self._revisions[plan.plan_id] += 1

    async def get_plan_record(self, plan_id: str) -> PlanStoreRecord | None:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                return None
            return PlanStoreRecord(plan=plan, revision=self._revisions[plan_id])

    async def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        with self._lock:
            if plan.plan_id not in self._plans:
                raise PlanNotFoundError(plan.plan_id)
            if self._revisions[plan.plan_id] != expected_revision:
                return False
            self._plans[plan.plan_id] = plan
            self._revisions[plan.plan_id] += 1
            return True

    async def save_plan_if_claimed(
        self,
        plan: PlanState,
        claim: PlanClaimRecord,
        *,
        expected_revision: int,
        now: float,
    ) -> bool:
        claim_store = self._claim_store
        if not isinstance(claim_store, InMemoryPlanClaimStore):
            return False
        with claim_store._lock:
            with self._lock:
                if plan.plan_id not in self._plans:
                    raise PlanNotFoundError(plan.plan_id)
                current_claim = claim_store._claims.get(plan.plan_id)
                if current_claim != claim or claim.lease_expires_at <= float(now):
                    return False
                if self._revisions[plan.plan_id] != expected_revision:
                    return False
                self._plans[plan.plan_id] = plan
                self._revisions[plan.plan_id] += 1
                return True

    def bind_claim_store(self, claim_store: object) -> None:
        """绑定用于本地原子保存检查的 Claim Store。"""

        self._claim_store = claim_store

    async def get_plan(self, plan_id: str) -> PlanState | None:
        with self._lock:
            return self._plans.get(plan_id)

    async def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
        with self._lock:
            plans = list(self._plans.values())
        if owner_agent_id is None:
            return plans
        return [plan for plan in plans if plan.owner_agent_id == owner_agent_id]


class InMemoryPlanClaimStore:
    """线程安全的本地 Plan Claim/Lease Store。"""

    def __init__(self) -> None:
        self._claims: dict[str, PlanClaimRecord] = {}
        self._lock = RLock()

    async def claim_plan(
        self,
        *,
        plan_id: str,
        owner_agent_id: str,
        worker_id: str,
        lease_seconds: float,
        now: float,
    ) -> PlanClaimResult:
        self._validate_non_empty(plan_id, field_name="plan_id")
        self._validate_non_empty(owner_agent_id, field_name="owner_agent_id")
        self._validate_non_empty(worker_id, field_name="worker_id")
        lease_seconds = float(lease_seconds)
        now = float(now)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")

        with self._lock:
            existing = self._claims.get(plan_id)
            if (
                existing is not None
                and existing.lease_expires_at > now
                and (
                    existing.owner_agent_id != owner_agent_id
                    or existing.worker_id != worker_id
                )
            ):
                return PlanClaimResult(status="busy", existing_claim=existing)
            generation = 1 if existing is None else existing.generation + 1
            claim = PlanClaimRecord(
                plan_id=plan_id,
                owner_agent_id=owner_agent_id,
                worker_id=worker_id,
                claimed_at=now,
                lease_expires_at=now + lease_seconds,
                generation=generation,
            )
            self._claims[plan_id] = claim
            return PlanClaimResult(status="claimed", claim=claim)

    async def release_plan(
        self,
        *,
        plan_id: str,
        worker_id: str,
        owner_agent_id: str | None = None,
    ) -> bool:
        self._validate_non_empty(plan_id, field_name="plan_id")
        self._validate_non_empty(worker_id, field_name="worker_id")
        if owner_agent_id is not None:
            self._validate_non_empty(owner_agent_id, field_name="owner_agent_id")
        with self._lock:
            existing = self._claims.get(plan_id)
            if (
                existing is None
                or existing.worker_id != worker_id
                or (
                    owner_agent_id is not None
                    and existing.owner_agent_id != owner_agent_id
                )
            ):
                return False
            del self._claims[plan_id]
            return True

    async def get_claim(self, plan_id: str) -> PlanClaimRecord | None:
        self._validate_non_empty(plan_id, field_name="plan_id")
        with self._lock:
            return self._claims.get(plan_id)

    async def expired_claims(
        self,
        *,
        now: float,
        owner_agent_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[PlanClaimRecord, ...]:
        now = float(now)
        if owner_agent_id is not None:
            self._validate_non_empty(owner_agent_id, field_name="owner_agent_id")
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        with self._lock:
            claims = [
                claim
                for claim in self._claims.values()
                if claim.lease_expires_at <= now
                and (owner_agent_id is None or claim.owner_agent_id == owner_agent_id)
            ]
        claims.sort(key=lambda claim: (claim.lease_expires_at, claim.plan_id))
        if limit is not None:
            claims = claims[:limit]
        return tuple(claims)

    async def release_expired_claim(
        self,
        claim: PlanClaimRecord,
        *,
        now: float,
    ) -> bool:
        now = float(now)
        with self._lock:
            existing = self._claims.get(claim.plan_id)
            if existing != claim or existing.lease_expires_at > now:
                return False
            del self._claims[claim.plan_id]
            return True

    def _validate_non_empty(self, value: str, *, field_name: str) -> None:
        if not value.strip():
            raise ValueError(f"{field_name} must not be empty")
