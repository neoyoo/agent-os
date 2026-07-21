from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentos.distributed._delivery_models import RunDeliveryTarget
from agentos.distributed._model_validation import (
    normalize_utc,
    require_identifier,
    require_non_negative,
    require_positive,
)
from agentos.runtime.run_state import RunStatus


@dataclass(frozen=True, slots=True)
class CommittedExecutionOutcome:
    """一次 execution delivery 已提交 checkpoint 的内部恢复事实。"""

    target: RunDeliveryTarget
    turn_id: str
    execution_attempt: int
    committed_version: int
    committed_at: datetime

    def __post_init__(self) -> None:
        if type(self.target) is not RunDeliveryTarget:
            raise TypeError("target must be RunDeliveryTarget")
        require_identifier(self.turn_id, "turn_id")
        require_positive(self.execution_attempt, "execution_attempt")
        require_non_negative(self.committed_version, "committed_version")
        if self.committed_version > self.target.run.aggregate_version:
            raise ValueError("committed version exceeds current run version")
        if self.is_current and self.target.run.status not in {
            RunStatus.WAITING,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            raise ValueError("current committed outcome requires an observable status")
        object.__setattr__(
            self,
            "committed_at",
            normalize_utc(self.committed_at, "committed_at"),
        )

    @property
    def is_current(self) -> bool:
        """该 checkpoint 是否仍是 Run 当前权威版本。"""

        return self.committed_version == self.target.run.aggregate_version


__all__ = ["CommittedExecutionOutcome"]
