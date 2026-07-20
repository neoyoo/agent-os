from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from agentos.planning.models import PlanState


PlanClaimStatus = Literal["claimed", "busy"]


@dataclass(frozen=True, slots=True)
class PlanStoreRecord:
    """Plan 载荷和 Store 拥有的乐观并发修订号。"""

    plan: PlanState
    revision: int


@dataclass(frozen=True, slots=True)
class PlanClaimRecord:
    """一个可调度 Plan 的 Lease 记录。"""

    plan_id: str
    owner_agent_id: str
    worker_id: str
    claimed_at: float
    lease_expires_at: float
    generation: int

    def as_dict(self) -> dict[str, object]:
        """返回 JSON-safe Claim 记录。"""

        return {
            "plan_id": self.plan_id,
            "owner_agent_id": self.owner_agent_id,
            "worker_id": self.worker_id,
            "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True)
class PlanClaimResult:
    """一次 Plan Claim 尝试的结果。"""

    status: PlanClaimStatus
    claim: PlanClaimRecord | None = None
    existing_claim: PlanClaimRecord | None = None

    def as_dict(self) -> dict[str, object]:
        """返回 JSON-safe Claim 结果。"""

        return {
            "status": self.status,
            "claim": self.claim.as_dict() if self.claim is not None else None,
            "existing_claim": (
                self.existing_claim.as_dict()
                if self.existing_claim is not None
                else None
            ),
        }


class PlanStore(Protocol):
    """Plan State 的真值存储边界。"""

    async def create_plan(self, plan: PlanState) -> None: ...

    async def save_plan(self, plan: PlanState) -> None: ...

    async def get_plan(self, plan_id: str) -> PlanState | None: ...

    async def list_plans(
        self,
        owner_agent_id: str | None = None,
    ) -> list[PlanState]: ...


class CompareAndSavePlanStore(Protocol):
    """支持乐观并发控制的可选 PlanStore API。"""

    async def get_plan_record(self, plan_id: str) -> PlanStoreRecord | None: ...

    async def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool: ...


class ClaimGuardedPlanStore(Protocol):
    """支持 Claim 守卫原子保存的可选 PlanStore API。"""

    async def save_plan_if_claimed(
        self,
        plan: PlanState,
        claim: "PlanClaimRecord",
        *,
        expected_revision: int,
        now: float,
    ) -> bool: ...


class PlanClaimStore(Protocol):
    """Planner Scheduler Worker 的临时 Claim/Lease 边界。"""

    async def claim_plan(
        self,
        *,
        plan_id: str,
        owner_agent_id: str,
        worker_id: str,
        lease_seconds: float,
        now: float,
    ) -> PlanClaimResult: ...

    async def release_plan(
        self,
        *,
        plan_id: str,
        worker_id: str,
        owner_agent_id: str | None = None,
    ) -> bool: ...

    async def get_claim(self, plan_id: str) -> PlanClaimRecord | None: ...


class PlanClaimSweepStore(Protocol):
    """列出并释放过期 Plan Claim 的可选边界。"""

    async def expired_claims(
        self,
        *,
        now: float,
        owner_agent_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[PlanClaimRecord, ...]: ...

    async def release_expired_claim(
        self,
        claim: PlanClaimRecord,
        *,
        now: float,
    ) -> bool: ...
