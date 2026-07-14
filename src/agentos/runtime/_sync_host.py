from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from concurrent.futures import Future
from contextvars import Context, copy_context
import threading
from types import TracebackType
from typing import TypeVar, cast

from agentos.runtime.errors import (
    SyncAdapterEventLoopError,
    SyncAdapterReentryError,
    SyncAgentClosedError,
)
from agentos.runtime._sync_submission import (
    _SubmissionFuture,
    _close_native_coroutine,
    _complete_submission,
    _set_future_exception,
    _settle_completion_task,
)


T = TypeVar("T")
_SENTINEL = object()
_OPEN = "open"
_CLOSING = "closing"
_CLOSED = "closed"


class SyncHost:
    """在单一 owner thread 中长期持有并驱动一个 asyncio Runner。"""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._state = _OPEN
        self._owner_thread_id: int | None = None
        self._owner_terminal_error: BaseException | None = None
        self._loop: asyncio.AbstractEventLoop
        self._queue: (
            asyncio.Queue[
                tuple[Awaitable[object], _SubmissionFuture[object], Context] | object
            ]
            | None
        ) = None
        self._thread = threading.Thread(
            target=self._owner_main,
            name="agentos-sync-owner",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()
        if self._owner_terminal_error is not None:
            self._thread.join()
            raise RuntimeError("SyncHost owner thread failed to start") from (
                self._owner_terminal_error
            )

    def __enter__(self) -> SyncHost:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def submit(self, awaitable: Awaitable[T]) -> Future[T]:
        """把 awaitable 线性化提交到唯一 owner loop。"""

        rejection: BaseException | None = None
        rejection_cause: BaseException | None = None
        target: _SubmissionFuture[T] | None = None
        with self._lifecycle_lock:
            if self._state != _OPEN:
                rejection = SyncAgentClosedError("SyncHost is closing or closed")
            elif threading.get_ident() == self._owner_thread_id:
                rejection = SyncAdapterReentryError(
                    "synchronous submission cannot reenter its owner thread",
                )
            else:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    target = _SubmissionFuture()
                    item = (
                        cast(Awaitable[object], awaitable),
                        cast(_SubmissionFuture[object], target),
                        copy_context(),
                    )
                    assert self._queue is not None
                    try:
                        self._loop.call_soon_threadsafe(
                            self._queue.put_nowait,
                            item,
                        )
                    except RuntimeError as error:
                        rejection = SyncAgentClosedError(
                            "SyncHost owner loop is closed",
                        )
                        rejection_cause = error
                else:
                    rejection = SyncAdapterEventLoopError(
                        "synchronous submission cannot run inside an event loop",
                    )

        if rejection is not None:
            _close_native_coroutine(cast(Awaitable[object], awaitable))
            if rejection_cause is not None:
                raise rejection from rejection_cause
            raise rejection
        assert target is not None
        return target

    def close(self) -> None:
        """拒绝新提交，收敛已接受工作并等待 owner thread 退出。"""

        self._raise_if_owner_thread_reentry()
        with self._lifecycle_lock:
            if self._state == _OPEN:
                self._state = _CLOSING
                assert self._queue is not None
                try:
                    self._loop.call_soon_threadsafe(
                        self._queue.put_nowait,
                        _SENTINEL,
                    )
                except RuntimeError as error:
                    self._state = _OPEN
                    raise SyncAgentClosedError(
                        "SyncHost owner loop rejected close",
                    ) from error

        self._closed.wait()
        self._thread.join()

    def _raise_if_owner_thread_reentry(self) -> None:
        if threading.get_ident() == self._owner_thread_id:
            raise SyncAdapterReentryError(
                "SyncHost cannot be closed from its owner thread",
            )

    def _owner_main(self) -> None:
        try:
            with asyncio.Runner() as runner:
                self._owner_thread_id = threading.get_ident()
                self._loop = runner.get_loop()
                self._queue = asyncio.Queue()
                self._ready.set()
                runner.run(self._dispatcher())
        except BaseException as error:
            self._owner_terminal_error = error
            self._ready.set()
        finally:
            self._settle_queued_after_owner_exit()
            with self._lifecycle_lock:
                self._state = _CLOSED
                self._owner_thread_id = None
            self._closed.set()

    async def _dispatcher(self) -> None:
        assert self._queue is not None
        active: set[asyncio.Task[None]] = set()
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                break

            awaitable, target, context = cast(
                tuple[Awaitable[object], _SubmissionFuture[object], Context],
                item,
            )
            try:
                should_run = target.try_start()
            except BaseException as error:
                _close_native_coroutine(awaitable)
                if not target.done():
                    _set_future_exception(target, error)
                continue
            if not should_run:
                _close_native_coroutine(awaitable)
                continue

            completion = _complete_submission(awaitable, target)
            try:
                task = asyncio.create_task(completion, context=context)
            except BaseException as error:
                completion.close()
                _close_native_coroutine(awaitable)
                _set_future_exception(target, error)
                continue
            active.add(task)
            task.add_done_callback(
                lambda completed, tasks=active, submission_target=target,
                submitted=awaitable: self._consume_completed_task(
                    tasks,
                    completed,
                    submission_target,
                    submitted,
                ),
            )

        if active:
            await asyncio.gather(*tuple(active), return_exceptions=True)

    @staticmethod
    def _consume_completed_task(
        active: set[asyncio.Task[None]],
        completed: asyncio.Task[None],
        target: _SubmissionFuture[object],
        awaitable: Awaitable[object],
    ) -> None:
        try:
            active.discard(completed)
            _settle_completion_task(completed, target, awaitable)
        except BaseException:
            pass

    def _settle_queued_after_owner_exit(self) -> None:
        if self._queue is None:
            return
        owner_error = self._owner_terminal_error or RuntimeError(
            "SyncHost owner thread exited",
        )
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is _SENTINEL:
                continue
            awaitable, target, _ = cast(
                tuple[Awaitable[object], _SubmissionFuture[object], Context],
                item,
            )
            _close_native_coroutine(awaitable)
            if not target.done():
                _set_future_exception(target, owner_error)
