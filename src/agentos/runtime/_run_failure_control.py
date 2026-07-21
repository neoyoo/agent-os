from __future__ import annotations

from typing import Protocol

from agentos.runtime.run_commit import RunTerminalStatus
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.stream_events import TurnStreamFailed
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle


class RunFailureTarget(Protocol):
    runs: RunRuntime
    turns: TurnLifecycle
    _execution_guards: dict[str, RunWriteGuard]
    _turns: dict[str, TurnState | None]
    _uncertain_commits: set[str]

    def _require_guard(self, run_id: str) -> RunWriteGuard: ...

    async def _commit_terminal(
        self,
        *,
        run_id: str,
        guard: RunWriteGuard,
        status: RunTerminalStatus,
        turn_id: str | None,
    ) -> RunWriteGuard: ...


async def fail_open_run(
    target: RunFailureTarget,
    run_id: str,
    error: Exception,
) -> TurnStreamFailed:
    """提交当前运行中 execution 的权威失败。"""

    if run_id in target._uncertain_commits:
        raise error
    state = await target.runs.get_run(run_id)
    turn = target._turns.get(run_id)
    if state.status is not RunStatus.RUNNING:
        align_terminal_turn(turn, state.status)
        raise error
    try:
        guard = await target._commit_terminal(
            run_id=run_id,
            guard=target._require_guard(run_id),
            status="failed",
            turn_id=None if turn is None else turn.id,
        )
    except BaseException:
        target._uncertain_commits.add(run_id)
        raise
    target._execution_guards[run_id] = guard
    return target.turns.fail(turn, error)


def align_terminal_turn(turn: TurnState | None, status: RunStatus | None) -> None:
    if turn is None or turn.status != "running":
        return
    if status is RunStatus.WAITING:
        turn.mark_waiting()
    elif status is RunStatus.COMPLETED:
        turn.complete()
    elif status is RunStatus.CANCELLED:
        turn.cancel()


__all__ = ["align_terminal_turn", "fail_open_run"]
