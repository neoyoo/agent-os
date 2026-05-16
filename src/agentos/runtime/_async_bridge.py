from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
import threading
from typing import TypeVar


T = TypeVar("T")


async def iterate_sync_in_executor(
    factory: Callable[[], Iterator[T]],
    *,
    on_cancel: Callable[[], None] | None = None,
) -> AsyncIterator[T]:
    """在线程池中消费同步 iterator；取消时等待 worker 收口。"""

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[T | BaseException | None] = asyncio.Queue()
    stop_requested = threading.Event()

    def put(item: T | BaseException | None) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()

    def worker() -> None:
        try:
            for event in factory():
                if stop_requested.is_set():
                    break
                put(event)
                if stop_requested.is_set():
                    break
        except BaseException as error:
            if not stop_requested.is_set():
                put(error)
        finally:
            put(None)

    future = loop.run_in_executor(None, worker)
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
        await future
    except asyncio.CancelledError:
        stop_requested.set()
        if on_cancel is not None:
            on_cancel()
        await asyncio.shield(future)
        raise
