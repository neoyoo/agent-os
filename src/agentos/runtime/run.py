from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from agentos.attachments.types import Attachment
from agentos.runtime.stream_events import RunOptions


@dataclass(frozen=True, slots=True)
class UserTurnInput:
    """用户发起的新一轮输入。"""

    content: str
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalContinuationInput:
    """仅请求消费 Level 1 本地 notice 并创建 continuation turn。

    它不是恢复暂停的 Run，也不用于 durable resume 或 wakeup。
    """


RunInput: TypeAlias = str | UserTurnInput | LocalContinuationInput


@dataclass(frozen=True, slots=True)
class RunRequest:
    """规范化后的单次运行请求。"""

    input: UserTurnInput | LocalContinuationInput
    options: RunOptions = field(default_factory=RunOptions)


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
class AgentResult:
    """一次运行已完成的最终结果。"""

    content: str


@dataclass(frozen=True, slots=True)
class AgentWaiting:
    """一次运行已提交等待的结果。"""

    run_id: str
    reason: WaitReason


RunOutcome: TypeAlias = AgentResult | AgentWaiting
