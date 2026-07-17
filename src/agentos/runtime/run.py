from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from agentos._waiting import AgentWaiting


@dataclass(frozen=True, slots=True)
class RunOptions:
    """单次 agent run 的交互选项。"""

    thinking: bool = False
    show_thinking: bool = False


@dataclass(frozen=True, slots=True)
class UserTurnInput:
    """用户发起的新一轮输入。"""

    content: str
    artifact_handles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        handles = tuple(self.artifact_handles)
        if any(type(handle) is not str for handle in handles):
            raise TypeError("artifact handles must contain str values")
        object.__setattr__(self, "artifact_handles", handles)


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


@dataclass(frozen=True, slots=True)
class AgentResult:
    """一次运行已完成的最终结果。"""

    content: str


RunOutcome: TypeAlias = AgentResult | AgentWaiting
