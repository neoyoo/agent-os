from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agentos._waiting import WaitReason
from agentos.runtime.checkpoint import (
    RunCheckpoint,
    RuntimeCheckpointSource,
    SessionCheckpoint,
)
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.run_runtime import RunStore
from agentos.runtime.run_state import RunState
from agentos.runtime.session import SessionState
from agentos.runtime.waiting import WaitingCommit


CommandAcceptance = AcceptedContinuationInput | DurableCommandReceipt


class DurableStateStore(RunStore, Protocol):
    """Durable Profile 依赖的 Run、Command 和 Checkpoint Port。"""

    def initialize_session(self, session: SessionState) -> None: ...

    def bind_checkpoint_source(
        self,
        session_id: str,
        source: RuntimeCheckpointSource,
    ) -> None: ...

    def commit_waiting(
        self,
        *,
        session_id: str,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        expected_version: int,
    ) -> RunCheckpoint: ...

    def accept_command(
        self,
        *,
        session_id: str,
        command: DurableRunCommand,
        now: datetime,
    ) -> CommandAcceptance: ...

    def load_checkpoint(self, session_id: str) -> SessionCheckpoint | None: ...

    def load_pending_continuation(
        self,
        *,
        session_id: str,
        run_id: str,
    ) -> AcceptedContinuationInput | None: ...

    def recover_abandoned_runs(self, session_id: str) -> tuple[RunState, ...]: ...


@dataclass(frozen=True, slots=True)
class DurableWaitingRuntime:
    """把 WAITING 与恢复 checkpoint 原子提交到 Durable Store。"""

    session_id: str
    store: DurableStateStore
    checkpoint_source: RuntimeCheckpointSource | None = None

    async def commit_waiting(
        self,
        *,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        expected_version: int,
    ) -> WaitingCommit:
        self.store.commit_waiting(
            session_id=self.session_id,
            run_id=run_id,
            turn_id=turn_id,
            reason=reason,
            expected_version=expected_version,
        )
        return WaitingCommit(run_id=run_id, reason=reason)


@dataclass(frozen=True, slots=True)
class DurableCommandRuntime:
    """在 QueryLoop 之外接受并去重持久 Run Command。"""

    session_id: str
    store: DurableStateStore
    clock: Callable[[], datetime]

    def accept(self, command: DurableRunCommand) -> CommandAcceptance:
        """返回已接受的 continuation 或不进入 Loop 的 command receipt。"""

        if type(command) is not DurableRunCommand:
            raise TypeError("command must be DurableRunCommand")
        return self.store.accept_command(
            session_id=self.session_id,
            command=command,
            now=self.clock(),
        )

    def pending(self, run_id: str) -> AcceptedContinuationInput | None:
        """读取崩溃前已接受但尚未开始的 continuation。"""

        return self.store.load_pending_continuation(
            session_id=self.session_id,
            run_id=run_id,
        )


__all__ = [
    "CommandAcceptance",
    "DurableCommandRuntime",
    "DurableStateStore",
    "DurableWaitingRuntime",
]
