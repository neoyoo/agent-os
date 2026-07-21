from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from threading import RLock
from typing import Protocol, TypeAlias

from agentos.runtime._agent_stream_coordination import (
    OneShotSignal,
    PendingTerminalFailure,
    StreamState,
    TaskIdentity,
)
from agentos.runtime.errors import AgentStreamConsumerError
from agentos.runtime.stream_events import TurnStreamFailed


FailureControl: TypeAlias = Callable[[Exception], Awaitable[TurnStreamFailed]]


class _FailureTarget(Protocol):
    _state: StreamState
    _state_lock: RLock
    _consumer_task: asyncio.Task[object] | None
    _consumer_loop: asyncio.AbstractEventLoop | None
    _source_active: bool
    _iteration_done: OneShotSignal | None

    async def _finish_close(
        self,
        *,
        close_events: bool,
        original_error: BaseException | None,
    ) -> None: ...


class StreamFailureController:
    """管理内部 consumer failure control 及其一次性错误重抛。"""

    def __init__(self, control: FailureControl | None) -> None:
        self._control = control
        self._pending: PendingTerminalFailure | None = None

    def capture(
        self,
        error: BaseException,
        task: asyncio.Task[object],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._pending = PendingTerminalFailure(error, TaskIdentity(task, loop))

    def pop_for(
        self,
        task: asyncio.Task[object] | None,
        loop: asyncio.AbstractEventLoop,
    ) -> BaseException | None:
        pending = self._pending
        if pending is None or not pending.belongs_to(task, loop):
            return None
        self._pending = None
        return pending.error

    async def fail_active(
        self,
        target: _FailureTarget,
        error: Exception,
    ) -> TurnStreamFailed:
        if not isinstance(error, Exception):
            raise TypeError("error must be Exception")
        current_task = asyncio.current_task()
        current_loop = asyncio.get_running_loop()
        with target._state_lock:
            if (
                target._state is not StreamState.RUNNING
                or target._consumer_task is not current_task
                or target._consumer_loop is not current_loop
                or target._source_active
            ):
                raise AgentStreamConsumerError(
                    "agent stream failure requires its active consumer",
                )
            target._source_active = True
            target._iteration_done = OneShotSignal()
            iteration_done = target._iteration_done
        try:
            if self._control is None:
                raise AgentStreamConsumerError(
                    "agent stream does not support consumer failure control",
                )
            event = await self._control(error)
        except BaseException as injected_error:
            await target._finish_close(
                close_events=True,
                original_error=injected_error,
            )
            raise
        finally:
            with target._state_lock:
                target._source_active = False
            if not iteration_done.done:
                iteration_done.set_result()
        if type(event) is not TurnStreamFailed or event.error is not error:
            protocol_error = AgentStreamConsumerError(
                "agent stream source did not fail the active execution",
            )
            await target._finish_close(
                close_events=True,
                original_error=protocol_error,
            )
            raise protocol_error
        assert current_task is not None
        with target._state_lock:
            self.capture(error, current_task, current_loop)
        await target._finish_close(close_events=True, original_error=error)
        return event


__all__ = ["FailureControl", "StreamFailureController"]
