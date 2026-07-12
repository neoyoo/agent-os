from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos.runtime.run import WaitReason


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
        turn_id: str,
        reason: WaitReason,
    ) -> WaitingCommit: ...
