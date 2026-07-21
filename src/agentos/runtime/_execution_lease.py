from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from threading import Condition, Lock
from typing import TYPE_CHECKING

from agentos.runtime._agent_stream_coordination import (
    CleanupCallback,
    PendingSyncWork,
)
from agentos.runtime.errors import AgentBusyError
from agentos.runtime.stream_events import TurnStreamEvent, TurnStreamFailed

if TYPE_CHECKING:
    from agentos.runtime.agent_stream import AgentStream


class ExecutionLease:
    """为单个 agent 执行提供不排队的进程内门闩。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._idle = Condition(self._lock)
        self._reservation: object | None = None
        self._reservation_owner: object | None = None
        self._close_reservation: object | None = None
        self._active: AgentStream | None = None

    def reserve(self) -> object:
        """在 Run prepare 前立即预留唯一 execution。"""

        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("execution reservation requires an asyncio task")
        if not self._lock.acquire(blocking=False):
            raise AgentBusyError("agent already has an active execution")
        try:
            if self._reservation is not None:
                if self._reservation_owner is owner:
                    return self._reservation
                raise AgentBusyError("agent already has an active execution")
            if (
                self._close_reservation is not None
                or self._active is not None
            ):
                raise AgentBusyError("agent already has an active execution")
            reservation = object()
            self._reservation = reservation
            self._reservation_owner = owner
            return reservation
        finally:
            self._lock.release()

    def open_stream(
        self,
        events: AsyncIterator[TurnStreamEvent],
        *,
        cleanup: CleanupCallback,
        pending_sync_work: PendingSyncWork | None = None,
        failure_control: Callable[[Exception], Awaitable[TurnStreamFailed]] | None = None,
    ) -> AgentStream:
        """立即获取 lease 并创建惰性的异步事件流。"""
        reservation = self.reserve()
        try:
            return self.open_reserved_stream(
                reservation,
                events,
                cleanup=cleanup,
                pending_sync_work=pending_sync_work,
                failure_control=failure_control,
            )
        finally:
            self.cancel_reservation(reservation)

    def open_reserved_stream(
        self,
        reservation: object,
        events: AsyncIterator[TurnStreamEvent],
        *,
        cleanup: CleanupCallback,
        pending_sync_work: PendingSyncWork | None = None,
        failure_control: Callable[[Exception], Awaitable[TurnStreamFailed]] | None = None,
    ) -> AgentStream:
        """把 prepare 前的 reservation 原子转换为活跃 Stream。"""
        from agentos.runtime.agent_stream import AgentStream

        loop = asyncio.get_running_loop()
        with self._idle:
            if (
                self._reservation is not reservation
                or self._reservation_owner is not asyncio.current_task()
            ):
                raise RuntimeError("execution reservation is not active")
            try:
                stream = AgentStream._create(
                    release=self._release,
                    events=events,
                    cleanup=cleanup,
                    pending_sync_work=pending_sync_work,
                    created_loop=loop,
                    failure_control=failure_control,
                )
            finally:
                self._reservation = None
                self._reservation_owner = None
                self._idle.notify_all()
            self._active = stream
            return stream

    def cancel_reservation(self, reservation: object) -> None:
        """释放尚未转换为 Stream 的 reservation。"""

        with self._idle:
            if self._reservation is reservation:
                self._reservation = None
                self._reservation_owner = None
                self._idle.notify_all()

    def reserve_close(self) -> object:
        """仅在 execution 空闲时阻止新 Run 进入 prepare。"""

        if not self._lock.acquire(blocking=False):
            raise AgentBusyError("agent has an active execution")
        try:
            if (
                self._reservation is not None
                or self._close_reservation is not None
                or self._active is not None
            ):
                raise AgentBusyError("agent has an active execution")
            reservation = object()
            self._close_reservation = reservation
            return reservation
        finally:
            self._lock.release()

    def cancel_close_reservation(self, reservation: object) -> None:
        """释放 Profile close 的临时入口屏障。"""

        with self._idle:
            if self._close_reservation is reservation:
                self._close_reservation = None
                self._idle.notify_all()

    def _release(self, stream: AgentStream) -> None:
        with self._idle:
            if self._active is not stream:
                return
            self._active = None
            self._idle.notify_all()

    def wait_until_idle(self) -> None:
        """阻塞调用线程，直到当前执行租约释放。"""

        with self._idle:
            self._idle.wait_for(
                lambda: (
                    self._reservation is None
                    and self._close_reservation is None
                    and self._active is None
                ),
            )

    def interrupt(self) -> bool:
        """中断当前 stream；空闲时立即返回 ``False``。"""
        with self._lock:
            stream = self._active
        if stream is None:
            return False
        return stream._interrupt()
