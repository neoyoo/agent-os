from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agentos.planning.dispatch import PlanDispatchReport
from agentos.planning.models import PlanStatus
from agentos.planning.store import PlanClaimRecord, PlanClaimResult


PlannerSchedulablePlanReason = Literal[
    "ready-steps",
    "due-retries",
    "pending-dispatch",
]
PlanClaimedSchedulerTickSkipReason = Literal["busy", "tick-failed", "claim-lost"]
PlanClaimSweepSkipReason = Literal["release-race"]


@dataclass(frozen=True, slots=True)
class PlanSchedulerRetryReset:
    """一次调度 Tick 中被重置的可重试 Step。"""

    plan_id: str
    step_id: str
    attempts: int


@dataclass(frozen=True, slots=True)
class PlanSchedulerTickReport:
    """一次有界调度 Tick 的瞬时报告。"""

    plan_id: str
    retry_resets: tuple[PlanSchedulerRetryReset, ...] = ()
    dispatch: PlanDispatchReport = field(
        default_factory=lambda: PlanDispatchReport(plan_id=""),
    )


@dataclass(frozen=True, slots=True)
class PlanClaimedSchedulerTickSkip:
    """Claim 调度批次中被跳过的 Plan。"""

    plan_id: str
    reason: PlanClaimedSchedulerTickSkipReason
    detail: str = ""
    claim_result: PlanClaimResult | None = None

    def as_dict(self) -> dict[str, object]:
        """返回可序列化的跳过原因。"""

        return {
            "plan_id": self.plan_id,
            "reason": self.reason,
            "detail": self.detail,
            "claim_result": (
                self.claim_result.as_dict()
                if self.claim_result is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class PlanClaimedSchedulerTickReport:
    """Claim Plan 后执行调度 Tick 的批次报告。"""

    worker_id: str
    claims: tuple[PlanClaimResult, ...] = ()
    tick_reports: tuple[PlanSchedulerTickReport, ...] = ()
    skipped: tuple[PlanClaimedSchedulerTickSkip, ...] = ()
    released_plan_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerSchedulablePlan:
    """包含可调度工作的 Plan 只读摘要。"""

    plan_id: str
    owner_agent_id: str
    status: PlanStatus
    ready_step_ids: tuple[str, ...] = ()
    retryable_step_ids: tuple[str, ...] = ()
    reasons: tuple[PlannerSchedulablePlanReason, ...] = ()
    updated_at: float = 0

    def as_dict(self) -> dict[str, object]:
        """返回可序列化的 Plan 摘要。"""

        return {
            "plan_id": self.plan_id,
            "owner_agent_id": self.owner_agent_id,
            "status": self.status,
            "ready_step_ids": list(self.ready_step_ids),
            "retryable_step_ids": list(self.retryable_step_ids),
            "reasons": list(self.reasons),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class PlanClaimSweepSkip:
    """未能安全释放的过期 Claim。"""

    plan_id: str
    reason: PlanClaimSweepSkipReason
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        """返回可序列化的跳过原因。"""

        return {
            "plan_id": self.plan_id,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class PlanClaimSweepReport:
    """一次过期 Plan Claim 清扫报告。"""

    checked_claims: tuple[PlanClaimRecord, ...] = ()
    released_claims: tuple[PlanClaimRecord, ...] = ()
    skipped_claims: tuple[PlanClaimSweepSkip, ...] = ()
    dry_run: bool = False
    now: float = 0
    owner_agent_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        """返回可序列化的清扫报告。"""

        return {
            "now": self.now,
            "owner_agent_id": self.owner_agent_id,
            "dry_run": self.dry_run,
            "checked_claims": [claim.as_dict() for claim in self.checked_claims],
            "released_claims": [
                claim.as_dict() for claim in self.released_claims
            ],
            "skipped_claims": [skip.as_dict() for skip in self.skipped_claims],
        }


__all__ = [
    "PlanClaimedSchedulerTickReport",
    "PlanClaimedSchedulerTickSkip",
    "PlanClaimedSchedulerTickSkipReason",
    "PlanClaimSweepReport",
    "PlanClaimSweepSkip",
    "PlanClaimSweepSkipReason",
    "PlanSchedulerRetryReset",
    "PlanSchedulerTickReport",
    "PlannerSchedulablePlan",
    "PlannerSchedulablePlanReason",
]
