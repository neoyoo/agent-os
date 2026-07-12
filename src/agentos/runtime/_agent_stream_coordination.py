from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum, auto
from threading import RLock
from typing import Protocol, TypeAlias


class PendingSyncWork(Protocol):
    async def wait_until_idle(self) -> None: ...


CleanupCallback: TypeAlias = Callable[[], Awaitable[None] | None]


class StreamState(Enum):
    CREATED = auto()
    RUNNING = auto()
    CLOSING = auto()
    CLOSED = auto()


def _consume_waiter_result(waiter: asyncio.Future[None]) -> None:
    try:
        waiter.result()
    except BaseException:
        pass


class OneShotSignal:
    """提供不会被单个 waiter 取消污染的跨 event loop 一次性信号。"""

    def __init__(self) -> None:
        self._future: Future[None] = Future()

    @property
    def done(self) -> bool:
        return self._future.done()

    async def wait(self) -> None:
        waiter = asyncio.wrap_future(self._future)
        try:
            await asyncio.shield(waiter)
        except asyncio.CancelledError:
            waiter.add_done_callback(_consume_waiter_result)
            raise

    def set_result(self) -> None:
        self._future.set_result(None)

    def set_exception(self, error: BaseException) -> None:
        self._future.set_exception(error)


@dataclass(frozen=True, slots=True)
class TaskIdentity:
    task: asyncio.Task[object]
    loop: asyncio.AbstractEventLoop


class CloseCoordinator:
    """集中管理 cleanup owner、调度占用和共享完成信号。"""

    def __init__(self) -> None:
        self._completion = OneShotSignal()
        self._owner: TaskIdentity | None = None
        self._schedule_reserved = False

    @property
    def done(self) -> bool:
        return self._completion.done

    @property
    def started(self) -> bool:
        return self._owner is not None

    def is_owner(
        self,
        task: asyncio.Task[object] | None,
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        return task is not None and self._owner == TaskIdentity(task, loop)

    def claim(
        self,
        task: asyncio.Task[object] | None,
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        if self._owner is not None or task is None:
            return False
        self._owner = TaskIdentity(task, loop)
        self._schedule_reserved = False
        return True

    def reserve_schedule(self) -> bool:
        if self._schedule_reserved:
            return False
        self._schedule_reserved = True
        return True

    def schedule_finished(self) -> None:
        self._schedule_reserved = False

    def can_take_over(self, *, source_active: bool) -> bool:
        return self._owner is None and not self._schedule_reserved and not source_active

    async def wait(self) -> None:
        await self._completion.wait()

    def publish(self, error: BaseException | None) -> None:
        if error is None:
            self._completion.set_result()
        else:
            self._completion.set_exception(error)
        self._owner = None


@dataclass(frozen=True, slots=True)
class PendingTerminalFailure:
    error: BaseException
    consumer: TaskIdentity

    def belongs_to(
        self, task: asyncio.Task[object] | None, loop: asyncio.AbstractEventLoop,
    ) -> bool:
        return task is not None and self.consumer == TaskIdentity(task, loop)


@dataclass(frozen=True, slots=True)
class CancelReservation:
    consumer: asyncio.Task[object]
    loop: asyncio.AbstractEventLoop
    iteration_done: OneShotSignal | None


class _InterruptTarget(Protocol):
    _state: StreamState
    _state_lock: RLock
    _consumer_task: asyncio.Task[object] | None
    _consumer_loop: asyncio.AbstractEventLoop | None
    _iteration_done: OneShotSignal | None
    _source_active: bool

    async def _finish_close(
        self, *, close_events: bool, original_error: BaseException | None,
    ) -> None: ...


class InterruptController:
    """管理中断状态、跨线程调度和失败回滚。"""

    def __init__(
        self,
        target: _InterruptTarget,
        close: CloseCoordinator,
        created_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._target = target
        self._close = close
        self._created_loop = created_loop
        self._cancel_requested = False

    def interrupt(self) -> bool:
        reservation: CancelReservation | None = None
        schedule_created = False
        with self._target._state_lock:
            if self._target._state is StreamState.CLOSED:
                return False
            if self._target._state is StreamState.CREATED:
                self._target._state = StreamState.CLOSING
                schedule_created = self._reserve_created_close()
            else:
                self._target._state = StreamState.CLOSING
                reservation = self.reserve_consumer_cancel(
                    self._target._consumer_task, self._target._consumer_loop,
                    self._target._iteration_done,
                )
        if schedule_created:
            self.schedule_created_close()
        elif reservation is not None:
            self.schedule_cancel(reservation)
        return True

    def _reserve_created_close(self) -> bool:
        if self._cancel_requested:
            return False
        self._cancel_requested = True
        return self._close.reserve_schedule()

    def reserve_consumer_cancel(
        self,
        consumer: asyncio.Task[object] | None,
        loop: asyncio.AbstractEventLoop | None,
        iteration_done: OneShotSignal | None,
    ) -> CancelReservation | None:
        if self._cancel_requested:
            return None
        self._cancel_requested = True
        if consumer is None or loop is None:
            return None
        self._close.reserve_schedule()
        return CancelReservation(consumer, loop, iteration_done)

    def schedule_created_close(self) -> None:
        try:
            self._created_loop.call_soon_threadsafe(self._schedule_close)
        except BaseException:
            self._rollback_schedule(StreamState.CREATED)
            raise

    def schedule_cancel(self, reservation: CancelReservation) -> None:
        self._schedule_delivery(self._deliver_cancel, reservation)

    def _schedule_delivery(
        self, callback: Callable[[CancelReservation], None],
        reservation: CancelReservation,
    ) -> None:
        try:
            reservation.loop.call_soon_threadsafe(callback, reservation)
        except BaseException:
            self._rollback_schedule(StreamState.RUNNING, reservation.consumer)
            raise

    def _rollback_schedule(
        self, state: StreamState, consumer: asyncio.Task[object] | None = None,
    ) -> None:
        with self._target._state_lock:
            self._close.schedule_finished()
            if self._close.started:
                return
            if consumer is not None and self._target._consumer_task is not consumer:
                return
            self._cancel_requested = False
            if self._target._state is StreamState.CLOSING:
                self._target._state = state

    def _deliver_cancel(self, reservation: CancelReservation) -> None:
        with self._target._state_lock:
            self._close.schedule_finished()
        try:
            self._schedule_interrupt_fallback(reservation.iteration_done)
        finally:
            reservation.consumer.cancel()

    def _schedule_close(self) -> None:
        async def close() -> None:
            try:
                await self._target._finish_close(close_events=True, original_error=None)
            except BaseException as error:
                self._created_loop.call_exception_handler(
                    {
                        "message": "scheduled AgentStream close failed",
                        "exception": error,
                    },
                )

        close_coro = close()
        try:
            self._created_loop.create_task(close_coro)
        except BaseException:
            close_coro.close()
            with self._target._state_lock:
                self._close.schedule_finished()
            raise

    def _schedule_interrupt_fallback(self, iteration_done: OneShotSignal | None) -> None:
        async def finish_if_needed() -> None:
            if iteration_done is not None and not iteration_done.done:
                await iteration_done.wait()
            if self._close.done:
                return
            with self._target._state_lock:
                can_take_over = self._close.can_take_over(
                    source_active=self._target._source_active,
                )
            if can_take_over:
                try:
                    await self._target._finish_close(close_events=True, original_error=None)
                except BaseException as error:
                    asyncio.get_running_loop().call_exception_handler(
                        {
                            "message": "AgentStream cancellation cleanup failed",
                            "exception": error,
                        },
                    )

        asyncio.get_running_loop().create_task(finish_if_needed())
