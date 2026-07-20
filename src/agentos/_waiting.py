from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeAlias


WaitReasonKind: TypeAlias = Literal[
    "human_input",
    "timer",
    "remote_result",
    "resource_availability",
    "retry_backoff",
    "side_effect_reconciliation",
]


@dataclass(frozen=True, slots=True)
class WaitReason:
    """一次运行进入等待状态的原因。"""

    kind: WaitReasonKind
    handle: str
    detail: str | None = None
    not_before: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "human_input",
            "timer",
            "remote_result",
            "resource_availability",
            "retry_backoff",
            "side_effect_reconciliation",
        }:
            raise ValueError("wait reason kind is invalid")
        if type(self.handle) is not str or not self.handle.strip():
            raise ValueError("wait reason handle must not be empty")
        if self.kind == "side_effect_reconciliation" and (
            len(self.handle) != len("operation_") + 32
            or not self.handle.startswith("operation_")
            or any(
                character not in "0123456789abcdef"
                for character in self.handle[len("operation_"):]
            )
        ):
            raise ValueError(
                "side effect reconciliation requires a canonical operation handle",
            )
        if self.detail is not None and type(self.detail) is not str:
            raise TypeError("wait reason detail must be str or None")
        timed = self.kind in {"timer", "retry_backoff"}
        if timed and self.not_before is None:
            raise ValueError("timed wait reason requires not_before")
        if not timed and self.not_before is not None:
            raise ValueError("not_before is only valid for timed wait reasons")
        if self.not_before is not None:
            if (
                not isinstance(self.not_before, datetime)
                or self.not_before.utcoffset() is None
            ):
                raise ValueError("wait reason not_before must be timezone-aware")
            object.__setattr__(self, "not_before", self.not_before.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class AgentWaiting:
    """一次运行已提交等待的结果。"""

    run_id: str
    reason: WaitReason


@dataclass(frozen=True, slots=True)
class WaitRequest:
    """工具明确请求当前运行切片进入等待状态。"""

    reason: WaitReason
