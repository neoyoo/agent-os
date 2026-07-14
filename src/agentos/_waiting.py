from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


WaitReasonKind: TypeAlias = Literal[
    "human_input",
    "timer",
    "remote_result",
    "resource_availability",
    "retry_backoff",
]


@dataclass(frozen=True, slots=True)
class WaitReason:
    """一次运行进入等待状态的原因。"""

    kind: WaitReasonKind
    handle: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AgentWaiting:
    """一次运行已提交等待的结果。"""

    run_id: str
    reason: WaitReason


@dataclass(frozen=True, slots=True)
class WaitRequest:
    """工具明确请求当前运行切片进入等待状态。"""

    reason: WaitReason
