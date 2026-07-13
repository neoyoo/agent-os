from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from concurrent.futures import Future
import inspect
import threading
from typing import TypeVar, cast


T = TypeVar("T")


def _close_native_coroutine(awaitable: Awaitable[object]) -> None:
    if inspect.iscoroutine(awaitable):
        coroutine = cast(Coroutine[object, object, object], awaitable)
        try:
            coroutine.close()
        except BaseException:
            pass


def _set_future_result(target: Future[object], result: object) -> None:
    try:
        target.set_result(result)
    except BaseException:
        pass


def _set_future_exception(
    target: Future[object],
    error: BaseException,
) -> None:
    try:
        target.set_exception(error)
    except BaseException:
        pass


class _SubmissionFuture(Future[T]):
    """用独立 gate 线性化开始与外部取消。"""

    def __init__(self) -> None:
        super().__init__()
        self._gate = threading.Lock()
        self._cancel_reserved = False
        self._started = False

    def try_start(self) -> bool:
        with self._gate:
            if self._cancel_reserved or self.done():
                return False
            self._started = True
            return True

    def cancel(self) -> bool:
        with self._gate:
            if self._started:
                return False
            self._cancel_reserved = True
        return super().cancel()

    def cancel_from_owner(self) -> bool:
        try:
            return super().cancel()
        except BaseException:
            return self.cancelled()


async def _complete_submission(
    awaitable: Awaitable[object],
    target: _SubmissionFuture[object],
) -> None:
    try:
        result = await awaitable
    except asyncio.CancelledError:
        target.cancel_from_owner()
    except BaseException as error:
        _set_future_exception(target, error)
    else:
        _set_future_result(target, result)
    finally:
        if not target.done():
            _set_future_exception(
                target,
                RuntimeError("SyncHost completion did not settle its target"),
            )


def _settle_completion_task(
    task: asyncio.Task[None],
    target: _SubmissionFuture[object],
    awaitable: Awaitable[object],
) -> None:
    try:
        if task.cancelled():
            _close_native_coroutine(awaitable)
            target.cancel_from_owner()
            return
        error = task.exception()
        if error is not None:
            _close_native_coroutine(awaitable)
            _set_future_exception(target, error)
        elif not target.done():
            _close_native_coroutine(awaitable)
            _set_future_exception(
                target,
                RuntimeError("SyncHost completion task did not settle target"),
            )
    except BaseException as error:
        _close_native_coroutine(awaitable)
        if not target.done():
            _set_future_exception(target, error)
    finally:
        if not target.done():
            _close_native_coroutine(awaitable)
            _set_future_exception(
                target,
                RuntimeError("SyncHost completion task left target pending"),
            )
