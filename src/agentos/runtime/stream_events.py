from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from agentos._waiting import WaitReason
from agentos.providers import ProviderResponse


@dataclass(frozen=True, slots=True)
class TurnStreamStarted:
    """agent turn stream 已开始。"""

    user_message: str


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    """用户可见的 agent 运行状态更新。"""

    stage: str
    message: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ContextLoaded:
    """本轮 provider 请求上下文已装载。"""

    source: Literal["runtime", "memory", "session", "attachment"] = "runtime"
    summary: str = "上下文已装载。"


@dataclass(frozen=True, slots=True)
class SkillLoaded:
    """skill 或 skill 资源已进入上下文。"""

    skill_name: str
    resource: str | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class PlanUpdated:
    """agent 计划或执行状态发生用户可见变化。"""

    summary: str
    status: Literal["created", "updated", "completed"] = "updated"


@dataclass(frozen=True, slots=True)
class AssistantContentDelta:
    """assistant content 增量。"""

    index: int
    text: str


@dataclass(frozen=True, slots=True)
class AssistantThinkingDelta:
    """assistant thinking 增量。"""

    index: int
    text: str


@dataclass(frozen=True, slots=True)
class AssistantCompleted:
    """assistant 最终响应已完成。"""

    response: ProviderResponse


@dataclass(frozen=True, slots=True)
class FinalResult:
    """agent 最终可见结果已形成。"""

    content: str


@dataclass(frozen=True, slots=True)
class ToolStreamStarted:
    """tool execution 已开始。"""

    tool_name: str
    tool_call_id: str


@dataclass(frozen=True, slots=True)
class ToolStreamCompleted:
    """tool execution 已完成。"""

    tool_name: str
    tool_call_id: str
    content: str


@dataclass(frozen=True, slots=True)
class ToolStreamFailed:
    """tool execution 失败。"""

    tool_name: str
    tool_call_id: str
    error: BaseException


@dataclass(frozen=True, slots=True)
class TurnStreamCompleted:
    """agent turn stream 已完成。"""

    content: str


@dataclass(frozen=True, slots=True)
class TurnStreamWaiting:
    """当前执行切片已提交权威等待状态。"""

    run_id: str
    reason: WaitReason


@dataclass(frozen=True, slots=True)
class TurnStreamFailed:
    """agent turn stream 失败。"""

    error: BaseException


@dataclass(frozen=True, slots=True)
class TurnStreamCancelled:
    """agent turn stream 被取消。"""

    reason: str | None = None


TurnStreamEvent: TypeAlias = (
    TurnStreamStarted
    | StatusUpdate
    | ContextLoaded
    | SkillLoaded
    | PlanUpdated
    | AssistantContentDelta
    | AssistantThinkingDelta
    | AssistantCompleted
    | FinalResult
    | ToolStreamStarted
    | ToolStreamCompleted
    | ToolStreamFailed
    | TurnStreamCompleted
    | TurnStreamWaiting
    | TurnStreamFailed
    | TurnStreamCancelled
)
