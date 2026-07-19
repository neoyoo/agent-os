from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos._waiting import WaitReason
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard


@dataclass(frozen=True, slots=True)
class WaitingCommit:
    """等待状态提交后的稳定标识。"""

    run_id: str
    reason: WaitReason
    aggregate_version: int

    def __post_init__(self) -> None:
        if type(self.aggregate_version) is not int or self.aggregate_version < 0:
            raise ValueError("waiting commit aggregate_version is invalid")


class WaitingRuntime(Protocol):
    """提交权威等待状态的边界。

    commit_waiting 成功返回表示权威状态已完成 RUNNING -> WAITING；
    调用失败不得解释为运行已经进入等待状态。
    """

    async def commit_waiting(
        self,
        *,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
    ) -> WaitingCommit: ...


@dataclass(frozen=True, slots=True)
class LocalWaitingRuntime:
    """Commit WAITING to the Level 1 in-memory RunRuntime."""

    runs: RunRuntime

    async def commit_waiting(
        self,
        *,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
    ) -> WaitingCommit:
        waiting = await self.runs.wait(
            run_id,
            reason=reason,
            guard=guard,
        )
        return WaitingCommit(run_id, reason, waiting.aggregate_version)
