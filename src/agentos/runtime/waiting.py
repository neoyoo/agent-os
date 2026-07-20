from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos._waiting import WaitReason
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.side_effect_types import WaitingToolCompletion


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
        completion: WaitingToolCompletion | None = None,
    ) -> WaitingCommit: ...


@dataclass(frozen=True, slots=True)
class LocalWaitingRuntime:
    """Commit WAITING to the Level 1 in-memory RunRuntime."""

    runs: RunRuntime
    side_effects: InMemorySideEffectStore

    async def commit_waiting(
        self,
        *,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
        completion: WaitingToolCompletion | None = None,
    ) -> WaitingCommit:
        async def commit_run() -> WaitingCommit:
            waiting = await self.runs.wait(
                run_id,
                reason=reason,
                guard=guard,
            )
            return WaitingCommit(run_id, reason, waiting.aggregate_version)

        if completion is None:
            return await commit_run()
        committed = await self.side_effects.commit_wait_control(
            completion=completion,
            session_id=self.runs.session_id,
            run_id=run_id,
            turn_id=turn_id,
            reason=reason,
            guard=guard,
            commit_run=commit_run,
        )
        if type(committed) is not WaitingCommit:
            raise TypeError("local waiting commit returned invalid metadata")
        return committed
