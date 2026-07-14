from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from threading import Condition, Lock
from typing import TYPE_CHECKING

from agentos.runtime._agent_stream_coordination import (
    CleanupCallback,
    PendingSyncWork,
)
from agentos.runtime.errors import AgentBusyError
from agentos.runtime.stream_events import TurnStreamEvent

if TYPE_CHECKING:
    from agentos.runtime.agent_stream import AgentStream


class ExecutionLease:
    """为单个 agent 执行提供不排队的进程内门闩。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._idle = Condition(self._lock)
        self._active: AgentStream | None = None

    def open_stream(
        self,
        events: AsyncIterator[TurnStreamEvent],
        *,
        cleanup: CleanupCallback,
        pending_sync_work: PendingSyncWork | None = None,
    ) -> AgentStream:
        """立即获取 lease 并创建惰性的异步事件流。"""
        from agentos.runtime.agent_stream import AgentStream

        loop = asyncio.get_running_loop()
        if not self._lock.acquire(blocking=False):
            raise AgentBusyError("agent already has an active execution")
        try:
            if self._active is not None:
                raise AgentBusyError("agent already has an active execution")
            stream = AgentStream._create(
                release=self._release,
                events=events,
                cleanup=cleanup,
                pending_sync_work=pending_sync_work,
                created_loop=loop,
            )
            self._active = stream
            return stream
        finally:
            self._lock.release()

    def _release(self, stream: AgentStream) -> None:
        with self._idle:
            if self._active is not stream:
                return
            self._active = None
            self._idle.notify_all()

    def wait_until_idle(self) -> None:
        """阻塞调用线程，直到当前执行租约释放。"""

        with self._idle:
            self._idle.wait_for(lambda: self._active is None)

    def interrupt(self) -> bool:
        """中断当前 stream；空闲时立即返回 ``False``。"""
        with self._lock:
            stream = self._active
        if stream is None:
            return False
        return stream._interrupt()
