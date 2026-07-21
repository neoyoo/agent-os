from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextvars import Context

from agentos.runtime.stream_events import TurnStreamEvent


class ContextualEventPump(AsyncIterator[TurnStreamEvent]):
    """在单个带上下文的 Task 中按需推进并关闭事件流。"""

    def __init__(
        self,
        events: AsyncIterator[TurnStreamEvent],
        *,
        context: Context,
    ) -> None:
        self._events = events
        self._requests: asyncio.Queue[asyncio.Future[TurnStreamEvent]] = (
            asyncio.Queue(maxsize=1)
        )
        self._failure: BaseException | None = None
        self._close_error: BaseException | None = None
        self._next_pending = False
        self._closed = False
        self._task = asyncio.create_task(self._run(), context=context)

    def __aiter__(self) -> ContextualEventPump:
        return self

    async def __anext__(self) -> TurnStreamEvent:
        if self._next_pending:
            raise RuntimeError("contextual event pump already has an active consumer")
        if self._closed or self._task.done():
            self._raise_finished()
        self._next_pending = True
        response = asyncio.get_running_loop().create_future()
        try:
            self._requests.put_nowait(response)
            done, _ = await asyncio.wait(
                (response, self._task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if response in done:
                return response.result()
            self._raise_finished()
        except asyncio.CancelledError:
            response.cancel()
            raise
        finally:
            self._next_pending = False

    async def aclose(self) -> None:
        self._closed = True
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        if self._failure is not None:
            raise self._failure
        if self._close_error is not None:
            raise self._close_error

    async def _run(self) -> None:
        iterator: AsyncIterator[TurnStreamEvent] | None = None
        active: asyncio.Future[TurnStreamEvent] | None = None
        try:
            iterator = self._events.__aiter__()
            while True:
                active = await self._requests.get()
                if active.cancelled():
                    active = None
                    continue
                try:
                    event = await anext(iterator)
                except StopAsyncIteration as error:
                    active.set_exception(error)
                    active = None
                    return
                except asyncio.CancelledError:
                    active.cancel()
                    active = None
                    raise
                except BaseException as error:
                    active.set_exception(error)
                    active = None
                    return
                active.set_result(event)
                active = None
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._failure = error
            if active is not None and not active.done():
                active.set_exception(error)
        finally:
            if active is not None and not active.done():
                active.cancel()
            if iterator is not None:
                try:
                    await _close_events(iterator)
                except BaseException as error:
                    self._close_error = error

    def _raise_finished(self) -> None:
        if self._failure is not None:
            raise self._failure
        if self._close_error is not None:
            raise self._close_error
        raise StopAsyncIteration


async def _close_events(events: AsyncIterator[TurnStreamEvent]) -> None:
    close = getattr(events, "aclose", None)
    if callable(close):
        await close()


__all__ = ["ContextualEventPump"]
