from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import Future
import threading
from typing import Generic, TypeVar


T = TypeVar("T")


class SyncIteratorAsyncBridge(Generic[T]):
    """把同步 iterator 放到线程池中，并提供可等待的关闭语义。"""

    def __init__(
        self,
        factory: Callable[[], Iterator[T]],
        *,
        on_cancel: Callable[[], None] | None = None,
        before_start: Callable[[asyncio.AbstractEventLoop], None] | None = None,
        after_worker: Callable[[], None] | None = None,
    ) -> None:
        self._factory = factory
        self._on_cancel = on_cancel
        self._before_start = before_start
        self._after_worker = after_worker
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[T | BaseException | None] | None = None
        self._future: asyncio.Future[None] | None = None
        self._stop_requested = threading.Event()
        self._closed = False

    def __aiter__(self) -> "SyncIteratorAsyncBridge[T]":
        self._ensure_started()
        return self

    async def __anext__(self) -> T:
        self._ensure_started()
        if self._closed:
            raise StopAsyncIteration
        assert self._queue is not None
        try:
            item = await self._queue.get()
        except asyncio.CancelledError:
            await self._aclose_from_cancelled_task()
            raise
        if item is None:
            self._closed = True
            await self._wait_for_worker()
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            self._closed = True
            await self._wait_for_worker()
            raise item
        return item

    async def aclose(self) -> None:
        """请求 worker 在下一个安全点停止，并等待线程退出。"""

        if self._closed:
            return
        self._closed = True
        self._stop_requested.set()
        if self._on_cancel is not None:
            self._on_cancel()
        await self._wait_for_worker()

    def _ensure_started(self) -> None:
        if self._future is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        if self._before_start is not None:
            self._before_start(self._loop)
        self._future = self._loop.run_in_executor(None, self._worker)

    def _put(self, item: T | BaseException | None) -> None:
        assert self._loop is not None
        assert self._queue is not None
        future: Future[None] = asyncio.run_coroutine_threadsafe(
            self._queue.put(item),
            self._loop,
        )
        future.result()

    def _worker(self) -> None:
        try:
            for event in self._factory():
                if self._stop_requested.is_set():
                    break
                self._put(event)
        except BaseException as error:
            if not self._stop_requested.is_set():
                self._put(error)
        finally:
            if self._after_worker is not None:
                self._after_worker()
            self._put(None)

    async def _wait_for_worker(self) -> None:
        if self._future is not None:
            await asyncio.shield(self._future)

    async def _aclose_from_cancelled_task(self) -> None:
        """Run close cleanup even though the current task has been cancelled."""

        task = asyncio.current_task()
        uncancel = getattr(task, "uncancel", None)
        pending_cancels = 0
        if callable(uncancel):
            while task is not None and task.cancelling():
                pending_cancels += 1
                uncancel()
        try:
            await self.aclose()
        finally:
            if task is not None:
                for _ in range(pending_cancels):
                    task.cancel()


def iterate_sync_in_executor(
    factory: Callable[[], Iterator[T]],
    *,
    on_cancel: Callable[[], None] | None = None,
    before_start: Callable[[asyncio.AbstractEventLoop], None] | None = None,
    after_worker: Callable[[], None] | None = None,
) -> AsyncIterator[T]:
    """在线程池中消费同步 iterator；取消或关闭时等待 worker 收口。"""

    return SyncIteratorAsyncBridge(
        factory,
        on_cancel=on_cancel,
        before_start=before_start,
        after_worker=after_worker,
    )
