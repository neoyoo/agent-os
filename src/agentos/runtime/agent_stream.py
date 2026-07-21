from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from threading import RLock
from types import TracebackType
from typing import Self, TypeAlias

from agentos.runtime._agent_stream_failure import (
    FailureControl,
    StreamFailureController,
)
from agentos.runtime._agent_stream_coordination import (
    CancelReservation,
    CleanupCallback,
    CloseCoordinator,
    InterruptController,
    OneShotSignal,
    PendingSyncWork,
    StreamState,
)
from agentos.runtime._agent_stream_cleanup import (
    finish_stream_close,
    wait_for_stream_close,
)
from agentos.runtime.errors import AgentStreamClosedError, AgentStreamConsumerError
from agentos.runtime.stream_events import (
    TurnStreamCancelled,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamWaiting,
)
_Release: TypeAlias = Callable[["AgentStream"], None]
_EventTransform: TypeAlias = Callable[
    [AsyncIterator[TurnStreamEvent]],
    AsyncIterator[TurnStreamEvent],
]


class AgentStream(AsyncIterator[TurnStreamEvent]):
    """拥有确定性关闭语义的单消费者异步事件流。"""

    def __init__(self) -> None:
        raise TypeError("AgentStream instances are created by the agent runtime")

    @classmethod
    def _create(
        cls,
        *,
        release: _Release,
        events: AsyncIterator[TurnStreamEvent],
        cleanup: CleanupCallback,
        pending_sync_work: PendingSyncWork | None,
        created_loop: asyncio.AbstractEventLoop,
        failure_control: FailureControl | None = None,
    ) -> Self:
        self = cls.__new__(cls)
        self._release = release
        self._events = events
        self._cleanup = cleanup
        self._pending_sync_work = pending_sync_work
        self._cleanup_error: BaseException | None = None
        self._failure = StreamFailureController(failure_control)
        self._state = StreamState.CREATED
        self._state_lock = RLock()
        self._consumer_task: asyncio.Task[object] | None = None
        self._consumer_loop: asyncio.AbstractEventLoop | None = None
        self._iteration_done: OneShotSignal | None = None
        self._source_active = False
        self._close_coordinator = CloseCoordinator()
        self._interrupt_controller = InterruptController(
            target=self,
            close=self._close_coordinator,
            created_loop=created_loop,
        )
        return self

    @property
    def closed(self) -> bool:
        """流是否已完成全部清理并释放 lease。"""
        with self._state_lock:
            return self._state is StreamState.CLOSED and self._close_coordinator.done

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> TurnStreamEvent:
        current_task = asyncio.current_task()
        current_loop = asyncio.get_running_loop()
        with self._state_lock:
            wait_for_close = self._state in {
                StreamState.CLOSING,
                StreamState.CLOSED,
            }
            cleanup_owner = self._close_coordinator.is_owner(current_task, current_loop)
            if not wait_for_close and self._state is StreamState.CREATED:
                self._state = StreamState.RUNNING
                self._consumer_task = current_task
                self._consumer_loop = current_loop
            elif not wait_for_close and (
                self._consumer_task is not current_task
                or self._consumer_loop is not current_loop
            ):
                raise AgentStreamConsumerError(
                    "agent stream already has a different consumer",
                )
            if not wait_for_close:
                self._source_active = True
                self._iteration_done = OneShotSignal()
                iteration_done = self._iteration_done
        if wait_for_close:
            if cleanup_owner:
                raise StopAsyncIteration
            await wait_for_stream_close(
                self._close_coordinator,
                suppress_failure=True,
            )
            with self._state_lock:
                terminal_error = self._failure.pop_for(current_task, current_loop)
            if terminal_error is not None:
                raise terminal_error
            raise StopAsyncIteration
        try:
            event = await anext(self._events)
        except StopAsyncIteration:
            await self._finish_close(close_events=True, original_error=None)
            raise
        except BaseException as error:
            await self._finish_close(close_events=True, original_error=error)
            raise
        finally:
            with self._state_lock:
                self._source_active = False
            if not iteration_done.done:
                iteration_done.set_result()
        if isinstance(event, TurnStreamFailed):
            assert current_task is not None
            with self._state_lock:
                self._failure.capture(event.error, current_task, current_loop)
            await self._finish_close(close_events=True, original_error=event.error)
        elif isinstance(
            event,
            (TurnStreamCompleted, TurnStreamWaiting, TurnStreamCancelled),
        ):
            await self._finish_close(close_events=True, original_error=None)
        return event

    async def __aenter__(self) -> Self:
        with self._state_lock:
            if self._state not in {StreamState.CREATED, StreamState.RUNNING}:
                raise AgentStreamClosedError("agent stream is closed")
            return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._close(original_error=exc)

    async def aclose(self) -> None:
        """幂等关闭流并等待 cleanup 完成。"""
        await self._close(original_error=None)

    async def _fail_active(self, error: Exception) -> TurnStreamFailed:
        """通过 runtime 内部控制口拒绝当前 event 并终结执行。"""
        return await self._failure.fail_active(self, error)

    def _transform_events(self, transform: _EventTransform) -> None:
        """在首次消费前替换事件投影，同时保留当前 Stream 句柄。"""

        with self._state_lock:
            if self._state is not StreamState.CREATED:
                raise RuntimeError("agent stream events are already active")
            self._events = transform(self._events)

    async def _close(self, *, original_error: BaseException | None) -> None:
        current_task = asyncio.current_task()
        current_loop = asyncio.get_running_loop()
        cancel_reservation: CancelReservation | None = None
        iteration_done: OneShotSignal | None = None

        with self._state_lock:
            if self._close_coordinator.is_owner(current_task, current_loop):
                return
            if self._state is StreamState.CLOSED or self._close_coordinator.started:
                wait_only = True
            elif self._state is StreamState.CREATED:
                self._state = StreamState.CLOSING
                wait_only = False
            elif self._consumer_task is current_task:
                self._state = StreamState.CLOSING
                wait_only = False
            else:
                wait_only = True
                self._state = StreamState.CLOSING
                iteration_done = self._iteration_done
                cancel_reservation = (
                    self._interrupt_controller.reserve_consumer_cancel(
                        self._consumer_task,
                        self._consumer_loop,
                        iteration_done,
                    )
                )

        if cancel_reservation is not None:
            self._interrupt_controller.schedule_cancel(
                cancel_reservation,
            )

        if iteration_done is not None and not iteration_done.done:
            await iteration_done.wait()

        if wait_only:
            if not self._close_coordinator.done:
                with self._state_lock:
                    can_take_over = self._close_coordinator.can_take_over(
                        source_active=self._source_active,
                    )
                if can_take_over:
                    await self._finish_close(close_events=True, original_error=original_error)
                    return
            await wait_for_stream_close(
                self._close_coordinator,
                suppress_failure=original_error is not None,
            )
            return

        await self._finish_close(close_events=True, original_error=original_error)

    async def _finish_close(
        self,
        *,
        close_events: bool,
        original_error: BaseException | None,
    ) -> None:
        await finish_stream_close(
            self,
            close_events=close_events,
            original_error=original_error,
        )

    def _interrupt(self) -> bool:
        return self._interrupt_controller.interrupt()
