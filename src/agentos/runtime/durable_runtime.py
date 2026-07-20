from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agentos._waiting import WaitReason
from agentos.runtime.checkpoint import (
    RunCheckpoint,
    SessionCheckpoint,
)
from agentos.runtime.durable_commands import (
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.execution import AcceptedTurnExecution
from agentos.runtime.run_runtime import RunStore, RunWriteGuard
from agentos.runtime.run_state import RunState
from agentos.runtime.session import SessionState
from agentos.runtime.run_commit import RunTerminalStatus
from agentos.runtime.side_effect_types import WaitingToolCompletion
from agentos.runtime.side_effect_store import SideEffectStore


CommandAcceptance = AcceptedTurnExecution | DurableCommandReceipt


class DurableStateStore(RunStore, Protocol):
    """Durable Profile 依赖的 Run、Command 和 Checkpoint Port。"""

    @property
    def side_effect_store(self) -> SideEffectStore: ...

    async def initialize_session(self, session: SessionState) -> None: ...

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
        completion: WaitingToolCompletion | None = None,
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

    async def accept_command(
        self,
        *,
        session_id: str,
        command: DurableRunCommand,
        now: datetime,
    ) -> CommandAcceptance: ...

    async def load_checkpoint(
        self,
        session_id: str,
    ) -> SessionCheckpoint | None: ...

    async def load_pending_continuation(
        self,
        *,
        session_id: str,
        run_id: str,
    ) -> AcceptedTurnExecution | None: ...

    async def recover_abandoned_runs(
        self,
        session_id: str,
    ) -> tuple[RunState, ...]: ...


@dataclass(frozen=True, slots=True)
class DurableCommandRuntime:
    """在 QueryLoop 之外接受并去重持久 Run Command。"""

    session_id: str
    store: DurableStateStore
    clock: Callable[[], datetime]

    async def accept(self, command: DurableRunCommand) -> CommandAcceptance:
        """返回已接受的 continuation 或不进入 Loop 的 command receipt。"""

        if type(command) is not DurableRunCommand:
            raise TypeError("command must be DurableRunCommand")
        return await self.store.accept_command(
            session_id=self.session_id,
            command=command,
            now=self.clock(),
        )

    async def pending(self, run_id: str) -> AcceptedTurnExecution | None:
        """读取崩溃前已接受但尚未开始的 continuation。"""

        return await self.store.load_pending_continuation(
            session_id=self.session_id,
            run_id=run_id,
        )


__all__ = [
    "CommandAcceptance",
    "DurableCommandRuntime",
    "DurableStateStore",
]
