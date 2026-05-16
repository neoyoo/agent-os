from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from queue import Empty, Queue
from typing import TypeVar

from agentos.providers import (
    ProviderResponse,
    ProviderStreamEvent,
    ProviderStreamOptions,
    complete_response_to_stream_events,
)


T = TypeVar("T")
_DONE = object()


def stream_async_provider_from_thread(
    *,
    loop: asyncio.AbstractEventLoop,
    async_stream_factory: Callable[[], AsyncIterator[ProviderStreamEvent]],
    cancel_requested: Callable[[], bool],
) -> Iterator[ProviderStreamEvent]:
    """从同步 worker 线程消费 event loop 上运行的 async provider stream。

    取消语义必须落到 event loop 侧的 asyncio.Task，而不是只取消
    run_coroutine_threadsafe 返回的 concurrent future。
    """

    output: Queue[ProviderStreamEvent | BaseException | object] = Queue()
    task = _start_loop_task(
        loop,
        _consume_async_stream(async_stream_factory, output),
    )
    yield from _drain_loop_task(
        loop=loop,
        task=task,
        output=output,
        cancel_requested=cancel_requested,
    )


def complete_async_provider_from_thread(
    *,
    loop: asyncio.AbstractEventLoop,
    async_complete_factory: Callable[[], Awaitable[ProviderResponse]],
    request_id: str,
    options: ProviderStreamOptions,
    cancel_requested: Callable[[], bool],
) -> Iterator[ProviderStreamEvent]:
    """从同步 worker 线程消费 async_complete，并转成 provider stream events。"""

    output: Queue[ProviderStreamEvent | BaseException | object] = Queue()
    task = _start_loop_task(
        loop,
        _consume_async_complete(
            async_complete_factory,
            output,
            request_id,
            options,
        ),
    )
    yield from _drain_loop_task(
        loop=loop,
        task=task,
        output=output,
        cancel_requested=cancel_requested,
    )


def _start_loop_task(
    loop: asyncio.AbstractEventLoop,
    coroutine: Awaitable[None],
) -> asyncio.Task[None]:
    """在目标 event loop 里显式创建并返回 asyncio.Task。"""

    async def start() -> asyncio.Task[None]:
        return asyncio.create_task(coroutine)

    return asyncio.run_coroutine_threadsafe(start(), loop).result()


async def _consume_async_stream(
    async_stream_factory: Callable[[], AsyncIterator[ProviderStreamEvent]],
    output: Queue[ProviderStreamEvent | BaseException | object],
) -> None:
    try:
        async for event in async_stream_factory():
            output.put(event)
    except asyncio.CancelledError as error:
        output.put(error)
        raise
    except BaseException as error:
        output.put(error)
    finally:
        output.put(_DONE)


async def _consume_async_complete(
    async_complete_factory: Callable[[], Awaitable[ProviderResponse]],
    output: Queue[ProviderStreamEvent | BaseException | object],
    request_id: str,
    options: ProviderStreamOptions,
) -> None:
    try:
        response = await async_complete_factory()
        for event in complete_response_to_stream_events(
            request_id=request_id,
            response=response,
            options=options,
        ):
            output.put(event)
    except asyncio.CancelledError as error:
        output.put(error)
        raise
    except BaseException as error:
        output.put(error)
    finally:
        output.put(_DONE)


def _drain_loop_task(
    *,
    loop: asyncio.AbstractEventLoop,
    task: asyncio.Task[None],
    output: Queue[ProviderStreamEvent | BaseException | object],
    cancel_requested: Callable[[], bool],
) -> Iterator[ProviderStreamEvent]:
    task_cancelled = False
    while True:
        if cancel_requested() and not task.done() and not task_cancelled:
            loop.call_soon_threadsafe(task.cancel)
            task_cancelled = True
        try:
            item = output.get(timeout=0.02)
        except Empty:
            continue
        if item is _DONE:
            return
        if isinstance(item, BaseException):
            if cancel_requested() and isinstance(item, asyncio.CancelledError):
                return
            raise item
        yield item
