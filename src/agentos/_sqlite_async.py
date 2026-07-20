from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

import aiosqlite

from agentos._sync_work import run_sync
from agentos.runtime._async_bridge import _await_cleanup_preserving_cancellation


_T = TypeVar("_T")


async def open_sqlite_connection(
    database_path: str | Path,
    *,
    isolation_level: str | None = None,
) -> aiosqlite.Connection:
    """Open SQLite without leaking its worker when the caller is cancelled."""

    path = Path(database_path)
    await run_sync(path.parent.mkdir, parents=True, exist_ok=True)

    async def connect() -> aiosqlite.Connection:
        return await aiosqlite.connect(path, isolation_level=isolation_level)

    task = asyncio.create_task(connect())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        async def finish_and_close() -> None:
            try:
                connection = await task
            except BaseException:
                return
            await connection.close()

        await _await_cleanup_preserving_cancellation(finish_and_close)
        raise


async def finish_sqlite_operation(
    operation: Callable[[], Awaitable[_T]],
    *,
    on_success: Callable[[], None] | None = None,
) -> _T:
    """Let an enqueued SQLite operation settle before propagating cancellation."""

    task = asyncio.create_task(operation())
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


@asynccontextmanager
async def sqlite_transaction(
    connection: aiosqlite.Connection,
    *,
    on_commit: Callable[[], None] | None = None,
) -> AsyncIterator[aiosqlite.Connection]:
    """Run one SQLite transaction without leaving cancellation-open state."""

    try:
        await finish_sqlite_operation(
            lambda: connection.execute("BEGIN IMMEDIATE"),
        )
    except BaseException:
        await finish_sqlite_operation(connection.rollback)
        raise
    try:
        yield connection
    except BaseException:
        await finish_sqlite_operation(connection.rollback)
        raise
    try:
        await finish_sqlite_operation(
            connection.commit,
            on_success=on_commit,
        )
    except asyncio.CancelledError:
        raise
    except BaseException:
        await finish_sqlite_operation(connection.rollback)
        raise


__all__ = [
    "finish_sqlite_operation",
    "open_sqlite_connection",
    "sqlite_transaction",
]
