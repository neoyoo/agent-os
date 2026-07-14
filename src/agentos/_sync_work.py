from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from typing import ParamSpec, TypeVar, cast


_P = ParamSpec("_P")
_T = TypeVar("_T")
_current_tracker: ContextVar[SyncWorkTracker | None] = ContextVar(
    "agentos_sync_work_tracker",
    default=None,
)


class SyncWorkTracker:
    """Track synchronous work that must converge before a Run releases its lease."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[object]] = set()
        self._exceptions: list[BaseException] = []

    @property
    def exceptions(self) -> tuple[BaseException, ...]:
        return tuple(self._exceptions)

    async def run(
        self,
        func: Callable[_P, _T],
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T:
        task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        tracked = cast(asyncio.Task[object], task)
        self._tasks.add(tracked)
        tracked.add_done_callback(self._work_finished)
        return await asyncio.shield(task)

    async def wait_until_idle(self) -> None:
        cancellation: asyncio.CancelledError | None = None
        while self._tasks:
            convergence = asyncio.gather(*tuple(self._tasks), return_exceptions=True)
            while not convergence.done():
                try:
                    await asyncio.shield(convergence)
                except asyncio.CancelledError as error:
                    cancellation = cancellation or error
            convergence.result()
        if cancellation is not None:
            raise cancellation

    def _work_finished(self, task: asyncio.Task[object]) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError as cancellation:
            error = cancellation
        if error is not None:
            self._exceptions.append(error)
        self._tasks.discard(task)


def current_sync_work_tracker() -> SyncWorkTracker | None:
    return _current_tracker.get()


async def run_sync(
    func: Callable[_P, _T],
    /,
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> _T:
    tracker = current_sync_work_tracker()
    if tracker is None:
        return await asyncio.to_thread(func, *args, **kwargs)
    return await tracker.run(func, *args, **kwargs)


async def bind_sync_work_tracker(
    events: AsyncIterator[_T],
    tracker: SyncWorkTracker,
) -> AsyncIterator[_T]:
    iterator = events.__aiter__()
    try:
        while True:
            token = _current_tracker.set(tracker)
            try:
                event = await anext(iterator)
            except StopAsyncIteration:
                return
            finally:
                _current_tracker.reset(token)
            yield event
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            token = _current_tracker.set(tracker)
            try:
                await close()
            finally:
                _current_tracker.reset(token)
