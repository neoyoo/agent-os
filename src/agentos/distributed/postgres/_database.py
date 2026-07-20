from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol, cast

from agentos.distributed.errors import (
    DistributedBackendUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed._cancel_safe import close_cancelled_acquisition


Row = Mapping[str, object]


class AsyncCursor(Protocol):
    async def fetchone(self) -> Row | None: ...

    async def fetchall(self) -> list[Row]: ...


class AsyncTransaction(Protocol):
    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> object: ...


class AsyncConnection(Protocol):
    async def execute(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> AsyncCursor: ...

    def transaction(self) -> AsyncTransaction: ...


class AsyncPool(Protocol):
    def connection(self) -> AbstractAsyncContextManager[AsyncConnection]: ...

    async def close(self) -> None: ...


class PostgresPool:
    """Native async psycopg pool owner with optional dependency isolation."""

    def __init__(
        self,
        pool: AsyncPool,
        *,
        database_errors: tuple[type[BaseException], ...] = (),
    ) -> None:
        self._pool: AsyncPool | None = pool
        self._database_errors = database_errors

    @classmethod
    async def open(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> PostgresPool:
        if type(dsn) is not str or not dsn.strip():
            raise ValueError("dsn must not be empty")
        if (
            type(min_size) is not int
            or type(max_size) is not int
            or min_size < 0
            or max_size < 1
            or min_size > max_size
        ):
            raise ValueError("postgres pool size is invalid")
        try:
            from psycopg import Error as PsycopgError
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool
        except ImportError:
            raise DistributedBackendUnavailableError() from None
        try:
            raw_pool = AsyncConnectionPool(
                conninfo=dsn,
                min_size=min_size,
                max_size=max_size,
                kwargs={"row_factory": dict_row},
                open=False,
            )
            acquisition = asyncio.create_task(raw_pool.open(wait=True))
            try:
                await asyncio.shield(acquisition)
            except asyncio.CancelledError as cancellation:
                await close_cancelled_acquisition(
                    acquisition,
                    lambda _: raw_pool.close(),
                )
                raise cancellation from None
        except Exception:
            raise DistributedBackendUnavailableError() from None
        return cls(
            cast(AsyncPool, raw_pool),
            database_errors=(PsycopgError,),
        )

    async def __aenter__(self) -> PostgresPool:
        self._require_open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        pool = self._require_open()
        try:
            async with pool.connection() as connection:
                yield connection
        except self._database_errors:
            raise DistributedBackendUnavailableError() from None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        async with self.connection() as connection:
            try:
                async with connection.transaction():
                    yield connection
            except self._database_errors:
                raise DistributedBackendUnavailableError() from None

    async def close(self) -> None:
        pool = self._pool
        if pool is None:
            return
        try:
            await pool.close()
        except self._database_errors:
            raise DistributedBackendUnavailableError() from None
        self._pool = None

    def _require_open(self) -> AsyncPool:
        if self._pool is None:
            raise DistributedStoreClosedError()
        return self._pool


async def fetchone(
    connection: AsyncConnection,
    query: str,
    params: Sequence[object] = (),
) -> Row | None:
    cursor = await connection.execute(query, params)
    return await cursor.fetchone()


async def fetchall(
    connection: AsyncConnection,
    query: str,
    params: Sequence[object] = (),
) -> list[Row]:
    cursor = await connection.execute(query, params)
    return await cursor.fetchall()


__all__ = [
    "AsyncConnection",
    "AsyncCursor",
    "AsyncPool",
    "PostgresPool",
    "Row",
    "fetchall",
    "fetchone",
]
