from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos._waiting import WaitReason
from agentos.runtime.run_runtime import RunRuntime


@dataclass(frozen=True, slots=True)
class WaitingCommit:
    """等待状态提交后的稳定标识。"""

    run_id: str
    reason: WaitReason


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
        expected_version: int,
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
        expected_version: int,
    ) -> WaitingCommit:
        if self.runs.get_run(run_id).aggregate_version != expected_version:
            raise RuntimeError("waiting run version conflict")
        self.runs.wait(run_id, reason=reason)
        return WaitingCommit(run_id, reason)
