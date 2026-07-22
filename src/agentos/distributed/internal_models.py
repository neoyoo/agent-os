from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from agentos._json_values import FrozenJsonObject
from agentos.distributed._model_validation import require_identifier, require_positive
from agentos.runtime.internal_start import normalize_internal_start_payload


InternalRunInputKind: TypeAlias = Literal["internal_start", "wakeup"]


@dataclass(frozen=True, slots=True, init=False)
class InternalRunSubmission:
    """只允许受信任 Worker 提交的 internal-start 输入。"""

    session_id: str
    submission_id: str
    source_kind: Literal["team_message"]
    source_payload: FrozenJsonObject

    def __init__(
        self,
        session_id: str,
        submission_id: str,
        source_kind: Literal["team_message"],
        source_payload: Mapping[str, object] | FrozenJsonObject,
    ) -> None:
        require_identifier(session_id, "session_id")
        require_identifier(submission_id, "submission_id")
        if source_kind != "team_message":
            raise ValueError("source_kind must be team_message")
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "submission_id", submission_id)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(
            self,
            "source_payload",
            normalize_internal_start_payload(source_payload),
        )


@dataclass(frozen=True, slots=True)
class InternalSubmissionAuthority:
    """绑定当前 TeamDelivery claim 的内部提交权限。"""

    delivery_id: str
    claim_id: str
    fence: int

    def __post_init__(self) -> None:
        require_identifier(self.delivery_id, "delivery_id")
        require_identifier(self.claim_id, "claim_id")
        require_positive(self.fence, "fence")


@dataclass(frozen=True, slots=True)
class InternalRunInputReceipt:
    """Team delivery 已由 PostgreSQL 接受的稳定 Run 输入事实。"""

    delivery_id: str
    session_id: str
    input_kind: InternalRunInputKind
    run_id: str
    aggregate_version: int

    def __post_init__(self) -> None:
        require_identifier(self.delivery_id, "delivery_id")
        require_identifier(self.session_id, "session_id")
        if self.input_kind not in {"internal_start", "wakeup"}:
            raise ValueError("input_kind is invalid")
        require_identifier(self.run_id, "run_id")
        require_positive(self.aggregate_version, "aggregate_version")


__all__ = [
    "InternalRunInputKind",
    "InternalRunInputReceipt",
    "InternalRunSubmission",
    "InternalSubmissionAuthority",
]
