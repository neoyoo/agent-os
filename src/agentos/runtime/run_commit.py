from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from agentos._waiting import WaitReason
from agentos.runtime.errors import RunProtocolError
from agentos.runtime.checkpoint import (
    RunCheckpoint,
    RuntimeCheckpointSource,
    SessionCheckpoint,
)
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunState
from agentos.runtime.waiting import WaitingRuntime


RunTerminalStatus: TypeAlias = Literal["completed", "failed", "cancelled"]


class CheckpointCommitStore(Protocol):
    """接收 immutable checkpoint 的原子持久化边界。"""

    async def commit_running(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        guard: RunWriteGuard,
    ) -> RunCheckpoint: ...

    async def commit_waiting(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
    ) -> RunCheckpoint: ...

    async def commit_terminal(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        status: RunTerminalStatus,
        guard: RunWriteGuard,
    ) -> RunCheckpoint: ...


@dataclass(frozen=True, slots=True)
class RunCommitRuntime:
    """统一提交 execution 的 WAITING 与终态。"""

    runs: RunRuntime
    waiting: WaitingRuntime
    checkpoint_source: RuntimeCheckpointSource | None = None
    checkpoint_store: CheckpointCommitStore | None = None

    async def commit_running(
        self,
        *,
        run_id: str,
        turn_id: str,
        cursor: RunExecutionCursor,
        guard: RunWriteGuard,
    ) -> RunWriteGuard:
        """在 Store 调用前捕获一次 RUNNING immutable checkpoint。"""

        source, store = self._checkpoint_boundary()
        if source is None or store is None:
            return guard
        checkpoint = source.capture(
            execution_cursor=cursor,
            run_id=run_id,
            turn_id=turn_id,
        )
        committed = await store.commit_running(
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            guard=guard,
        )
        return _next_checkpoint_guard(
            guard,
            committed,
            session_id=checkpoint.session_id,
            run_id=run_id,
            turn_id=turn_id,
        )

    async def commit_waiting(
        self,
        *,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
        active_refs: tuple[str, ...] | None = None,
    ) -> RunWriteGuard:
        """提交 WAITING 并返回推进后的 immutable guard。"""

        source, store = self._checkpoint_boundary()
        if source is not None and store is not None:
            checkpoint = source.capture(
                active_refs=active_refs,
                run_id=run_id,
                turn_id=turn_id,
            )
            committed = await store.commit_waiting(
                checkpoint=checkpoint,
                run_id=run_id,
                turn_id=turn_id,
                reason=reason,
                guard=guard,
            )
            return _next_checkpoint_guard(
                guard,
                committed,
                session_id=checkpoint.session_id,
                run_id=run_id,
                turn_id=turn_id,
            )
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

        source, store = self._checkpoint_boundary()
        if source is not None and store is not None:
            if turn_id is None:
                if status != "cancelled":
                    raise RunProtocolError(
                        "durable terminal commit requires turn_id",
                    )
                state = await self.runs.cancel(
                    run_id,
                    guard=guard,
                    turn_id=None,
                )
                return _next_guard(guard, state)
            checkpoint = source.capture(run_id=run_id, turn_id=turn_id)
            committed = await store.commit_terminal(
                checkpoint=checkpoint,
                run_id=run_id,
                turn_id=turn_id,
                status=status,
                guard=guard,
            )
            return _next_checkpoint_guard(
                guard,
                committed,
                session_id=checkpoint.session_id,
                run_id=run_id,
                turn_id=turn_id,
            )
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

    def _checkpoint_boundary(
        self,
    ) -> tuple[RuntimeCheckpointSource | None, CheckpointCommitStore | None]:
        if (self.checkpoint_source is None) != (self.checkpoint_store is None):
            raise RunProtocolError("checkpoint source and store must be configured together")
        return self.checkpoint_source, self.checkpoint_store

    @property
    def checkpointing(self) -> bool:
        """返回当前 Profile 是否配置 immutable checkpoint handoff。"""

        source, store = self._checkpoint_boundary()
        return source is not None and store is not None


def _next_guard(guard: RunWriteGuard, state: RunState) -> RunWriteGuard:
    return RunWriteGuard(
        expected_version=state.aggregate_version,
        claim_id=guard.claim_id,
        fencing_token=guard.fencing_token,
    )


def _next_checkpoint_guard(
    guard: RunWriteGuard,
    checkpoint: RunCheckpoint,
    *,
    session_id: str,
    run_id: str,
    turn_id: str,
) -> RunWriteGuard:
    if (
        checkpoint.session_id != session_id
        or checkpoint.run_id != run_id
        or checkpoint.turn_id != turn_id
        or checkpoint.aggregate_version != guard.expected_version + 1
    ):
        raise RunProtocolError("checkpoint store returned invalid commit metadata")
    return RunWriteGuard(
        expected_version=checkpoint.aggregate_version,
        claim_id=guard.claim_id,
        fencing_token=guard.fencing_token,
    )


__all__ = ["CheckpointCommitStore", "RunCommitRuntime", "RunTerminalStatus"]
