from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar


_T = TypeVar("_T")


async def close_cancelled_acquisition(
    acquisition: asyncio.Task[_T],
    close: Callable[[_T], Awaitable[None]],
) -> None:
    """Let acquisition and cleanup settle after its caller is cancelled."""

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
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
    cleanup.result()


__all__ = ["close_cancelled_acquisition"]
