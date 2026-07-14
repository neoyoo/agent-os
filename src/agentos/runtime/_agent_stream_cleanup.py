from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from threading import RLock
from typing import Protocol

from agentos.runtime._agent_stream_coordination import (
    CleanupCallback,
    CloseCoordinator,
    PendingSyncWork,
    StreamState,
)
from agentos.runtime.stream_events import TurnStreamEvent


class StreamCleanupTarget(Protocol):
    _events: AsyncIterator[TurnStreamEvent]
    _cleanup: CleanupCallback
    _pending_sync_work: PendingSyncWork | None
    _state: StreamState
    _state_lock: RLock
    _consumer_task: asyncio.Task[object] | None
    _consumer_loop: asyncio.AbstractEventLoop | None
    _close_coordinator: CloseCoordinator
    _release: Callable[[object], None]


async def wait_for_stream_close(
    coordinator: CloseCoordinator,
    *,
    suppress_failure: bool,
) -> None:
    try:
        await coordinator.wait()
    except asyncio.CancelledError:
        raise
    except BaseException:
        if not suppress_failure:
            raise


async def finish_stream_close(
    target: StreamCleanupTarget,
    *,
    close_events: bool,
    original_error: BaseException | None,
) -> None:
    current_task = asyncio.current_task()
    current_loop = asyncio.get_running_loop()
    with target._state_lock:
        if target._state is StreamState.CLOSED:
            owns_cleanup = False
        else:
            target._state = StreamState.CLOSING
            owns_cleanup = target._close_coordinator.claim(current_task, current_loop)
        cleanup_owner = target._close_coordinator.is_owner(current_task, current_loop)

    if not owns_cleanup:
        if cleanup_owner:
            return
        await wait_for_stream_close(
            target._close_coordinator,
            suppress_failure=original_error is not None,
        )
        return

    cancellation: asyncio.CancelledError | None = None
    cleanup_error: BaseException | None = None
    if close_events:
        close = getattr(target._events, "aclose", None)
        if close is not None:
            try:
                await close()
            except asyncio.CancelledError as error:
                cancellation = error
            except BaseException as error:
                cleanup_error = error
    if target._pending_sync_work is not None:
        try:
            await target._pending_sync_work.wait_until_idle()
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
        except BaseException as error:
            cleanup_error = cleanup_error or error
    try:
        result = target._cleanup()
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError as error:
        cancellation = cancellation or error
    except BaseException as error:
        cleanup_error = cleanup_error or error

    with target._state_lock:
        target._state = StreamState.CLOSED
        target._consumer_task = None
        target._consumer_loop = None
    try:
        target._release(target)
    except BaseException as error:
        cleanup_error = cleanup_error or error
    with target._state_lock:
        published_error = cleanup_error if original_error is None else None
        target._close_coordinator.publish(published_error)

    if cancellation is not None:
        raise cancellation
    if cleanup_error is not None and original_error is None:
        raise cleanup_error
