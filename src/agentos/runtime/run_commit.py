from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from agentos._waiting import WaitReason
from agentos.runtime.errors import RunProtocolError
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunState
from agentos.runtime.waiting import WaitingRuntime


RunTerminalStatus: TypeAlias = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class RunCommitRuntime:
    """统一提交 execution 的 WAITING 与终态。"""

    runs: RunRuntime
    waiting: WaitingRuntime

    async def commit_waiting(
        self,
        *,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
    ) -> RunWriteGuard:
        """提交 WAITING 并返回推进后的 immutable guard。"""

        commit = await self.waiting.commit_waiting(
            run_id=run_id,
            turn_id=turn_id,
            reason=reason,
            guard=guard,
        )
        if commit.run_id != run_id or commit.reason != reason:
            raise RunProtocolError("waiting runtime returned another run")
        return RunWriteGuard(
            expected_version=commit.aggregate_version,
            claim_id=guard.claim_id,
            fencing_token=guard.fencing_token,
        )

    async def commit_terminal(
        self,
        *,
        run_id: str,
        turn_id: str | None,
        status: RunTerminalStatus,
        guard: RunWriteGuard,
    ) -> RunWriteGuard:
        """提交 execution 终态并返回推进后的 immutable guard。"""

        if status == "completed":
            state = await self.runs.complete(
                run_id,
                guard=guard,
                turn_id=turn_id,
            )
        elif status == "failed":
            state = await self.runs.fail(
                run_id,
                guard=guard,
                turn_id=turn_id,
            )
        elif status == "cancelled":
            state = await self.runs.cancel(
                run_id,
                guard=guard,
                turn_id=turn_id,
            )
        else:
            raise ValueError("terminal run status is invalid")
        return _next_guard(guard, state)


def _next_guard(guard: RunWriteGuard, state: RunState) -> RunWriteGuard:
    return RunWriteGuard(
        expected_version=state.aggregate_version,
        claim_id=guard.claim_id,
        fencing_token=guard.fencing_token,
    )


__all__ = ["RunCommitRuntime", "RunTerminalStatus"]
