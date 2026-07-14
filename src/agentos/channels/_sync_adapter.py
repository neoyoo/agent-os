from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import ParamSpec, TypeVar, cast


_P = ParamSpec("_P")
_T = TypeVar("_T")


def _submit_sync_call(
    func: Callable[_P, _T],
    /,
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> asyncio.Future[_T]:
    loop = asyncio.get_running_loop()
    return cast(
        asyncio.Future[_T],
        loop.run_in_executor(None, partial(func, *args, **kwargs)),
    )


async def _settle_cancelled_call(
    future: asyncio.Future[_T],
    on_cancel_result: Callable[[_T], None] | None,
) -> None:
    task = asyncio.current_task()
    uncancel = getattr(task, "uncancel", None)
    pending_cancels = 0

    def drain_cancellations() -> None:
        nonlocal pending_cancels
        while task is not None and task.cancelling():
            pending_cancels += 1
            if callable(uncancel):
                uncancel()
            else:
                break

    cleanup_future: asyncio.Future[None] | None = None
    try:
        while not future.done():
            drain_cancellations()
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
        drain_cancellations()
        result = future.result()
        if on_cancel_result is not None:
            cleanup_future = _submit_sync_call(on_cancel_result, result)
            while not cleanup_future.done():
                drain_cancellations()
                try:
                    await asyncio.shield(cleanup_future)
                except asyncio.CancelledError:
                    continue
            drain_cancellations()
            cleanup_future.result()
    finally:
        drain_cancellations()
        if task is not None:
            for _ in range(pending_cancels):
                task.cancel()


async def _await_sync_call(
    future: asyncio.Future[_T],
    on_cancel_result: Callable[[_T], None] | None,
) -> _T:
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError as cancellation:
        try:
            await _settle_cancelled_call(future, on_cancel_result)
        except BaseException as cleanup_error:
            raise cancellation from cleanup_error
        raise


async def run_sync_call(
    func: Callable[_P, _T],
    /,
    *args: _P.args,
    on_cancel_result: Callable[[_T], None] | None = None,
    **kwargs: _P.kwargs,
) -> _T:
    """在线程中执行同步调用，并在调用方取消后等待 worker 收敛。"""

    future = _submit_sync_call(func, *args, **kwargs)
    return await _await_sync_call(future, on_cancel_result)


async def acquire_sync_resource(
    acquire: Callable[[str], _T],
    cleanup: Callable[[str, _T], None],
    resource_id: str,
) -> _T:
    """Acquire a sync resource and clean up a result returned after cancellation."""

    return await run_sync_call(
        acquire,
        resource_id,
        on_cancel_result=partial(cleanup, resource_id),
    )


@dataclass(slots=True)
class _SyncWorkLane:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    drainers: int = 0


class KeyedSyncWorkBoundary:
    """按资源 key 串行同步副作用，并在无使用者时回收 lane。"""

    def __init__(self) -> None:
        self._lanes: dict[str, _SyncWorkLane] = {}

    async def run(
        self,
        key: str,
        func: Callable[_P, _T],
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T:
        lane = self._lanes.setdefault(key, _SyncWorkLane())
        if lane.drainers:
            raise RuntimeError("synchronous work lane is draining")
        lane.users += 1
        try:
            async with lane.lock:
                return await run_sync_call(func, *args, **kwargs)
        finally:
            lane.users -= 1
            if lane.users == 0 and self._lanes.get(key) is lane:
                del self._lanes[key]

    async def drain(
        self,
        key: str,
        func: Callable[_P, _T],
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T:
        lane = self._lanes.setdefault(key, _SyncWorkLane())
        lane.users += 1
        lane.drainers += 1
        try:
            async with lane.lock:
                return await run_sync_call(func, *args, **kwargs)
        finally:
            lane.drainers -= 1
            lane.users -= 1
            if lane.users == 0 and self._lanes.get(key) is lane:
                del self._lanes[key]
