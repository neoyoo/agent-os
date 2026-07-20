from __future__ import annotations

from dataclasses import dataclass

from agentos._waiting import WaitReason
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.side_effect_types import WaitingToolCompletion
from agentos.runtime.tool_invocations import ToolInvocationPlan


@dataclass(frozen=True, slots=True)
class RunningCheckpointRequest:
    """请求 RunDriver 提交一个普通 RUNNING safe point。"""

    cursor: RunExecutionCursor


@dataclass(frozen=True, slots=True)
class PendingToolsCheckpointRequest:
    """在任何 Tool handler 启动前保护并提交 Provider tool batch。"""

    plan: ToolInvocationPlan

    def __post_init__(self) -> None:
        if type(self.plan) is not ToolInvocationPlan:
            raise TypeError("plan must be ToolInvocationPlan")


@dataclass(frozen=True, slots=True)
class WaitingCheckpointRequest:
    """描述 WAITING commit 使用的 ActiveWindow 投影。"""

    reason: WaitReason
    active_refs: tuple[str, ...]
    remove_active_refs: tuple[str, ...]
    completion: WaitingToolCompletion | None = None

    def __post_init__(self) -> None:
        if self.completion is not None and type(self.completion) is not WaitingToolCompletion:
            raise TypeError("completion must be WaitingToolCompletion or None")


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
