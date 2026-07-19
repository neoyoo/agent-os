from __future__ import annotations

from dataclasses import dataclass

from agentos._waiting import WaitReason
from agentos.providers import ProviderToolCall
from agentos.runtime.execution import RunExecutionCursor


@dataclass(frozen=True, slots=True)
class RunningCheckpointRequest:
    """请求 RunDriver 提交一个普通 RUNNING safe point。"""

    cursor: RunExecutionCursor


@dataclass(frozen=True, slots=True)
class PendingToolsCheckpointRequest:
    """在任何 Tool handler 启动前保护并提交 Provider tool batch。"""

    provider_call_index: int
    assistant_message_id: str
    calls: tuple[ProviderToolCall, ...]


@dataclass(frozen=True, slots=True)
class WaitingCheckpointRequest:
    """描述 WAITING commit 使用的 ActiveWindow 投影。"""

    reason: WaitReason
    active_refs: tuple[str, ...]
    remove_active_refs: tuple[str, ...]


ExecutionControl = (
    RunningCheckpointRequest
    | PendingToolsCheckpointRequest
    | WaitingCheckpointRequest
)


__all__ = [
    "ExecutionControl",
    "PendingToolsCheckpointRequest",
    "RunningCheckpointRequest",
    "WaitingCheckpointRequest",
]
