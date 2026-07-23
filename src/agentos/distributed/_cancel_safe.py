from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar


_T = TypeVar("_T")


async def close_cancelled_acquisition(
    acquisition: asyncio.Task[_T],
    close: Callable[[_T], Awaitable[None]],
    *,
    timeout: float | None = None,
) -> None:
    """Let acquisition and cleanup settle after its caller is cancelled."""

    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be positive")

    async def finish() -> None:
        try:
            resource = await acquisition
        except BaseException:
            return
        try:
            await close(resource)
        except BaseException:
            return

    cleanup = asyncio.create_task(finish())
    deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
    while not cleanup.done():
        try:
            if deadline is None:
                await asyncio.shield(cleanup)
                continue
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            done, _ = await asyncio.wait((cleanup,), timeout=remaining)
            if not done:
                break
        except asyncio.CancelledError:
            continue
    if not cleanup.done():
        cleanup.cancel()
        cleanup.add_done_callback(_consume_task_result)
        return
    cleanup.result()


def _consume_task_result(task: asyncio.Task[object]) -> None:
    try:
        task.result()
    except BaseException:
        pass


__all__ = ["close_cancelled_acquisition"]
