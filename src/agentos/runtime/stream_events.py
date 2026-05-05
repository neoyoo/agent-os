from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from agentos.providers import ProviderResponse, ProviderStreamEvent


@dataclass(frozen=True, slots=True)
class RunOptions:
    """单次 agent run 的交互选项。"""

    thinking: bool = False
    show_thinking: bool = False


@dataclass(frozen=True, slots=True)
class TurnStreamStarted:
    """agent turn stream 已开始。"""

    user_message: str


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
class TurnStreamFailed:
    """agent turn stream 失败。"""

    error: BaseException


@dataclass(frozen=True, slots=True)
class TurnStreamCancelled:
    """agent turn stream 被取消。"""

    reason: str | None = None


TurnStreamEvent: TypeAlias = (
    TurnStreamStarted
    | AssistantContentDelta
    | AssistantThinkingDelta
    | AssistantCompleted
    | ToolStreamStarted
    | ToolStreamCompleted
    | ToolStreamFailed
    | TurnStreamCompleted
    | TurnStreamFailed
    | TurnStreamCancelled
    | ProviderStreamEvent
)
