from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

from agentos.runtime._async_bridge import _await_cleanup_preserving_cancellation


_T = TypeVar("_T")


async def run_filesystem_operation(
    function: Callable[..., _T],
    *args: object,
    on_success: Callable[[], None] | None = None,
    **kwargs: object,
) -> _T:
    """Finish one filesystem operation before propagating cancellation."""

    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        async def finish() -> None:
            await task
            if on_success is not None:
                on_success()

        await _await_cleanup_preserving_cancellation(finish)
        raise
    if on_success is not None:
        on_success()
    return result


__all__ = ["run_filesystem_operation"]
