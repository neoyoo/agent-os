from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agentos.distributed.errors import (
    DistributedBackendUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.postgres._database import PostgresPool


class FakeDatabaseError(Exception):
    pass


class _ConnectionContext:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        if self.failure is not None:
            raise self.failure
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class FakePool:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.close_calls = 0

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(failure=self.failure)

    async def close(self) -> None:
        self.close_calls += 1


def test_postgres_module_import_does_not_load_optional_driver() -> None:
    script = """
import sys
import agentos.distributed.postgres._database
assert 'psycopg' not in sys.modules
assert 'psycopg_pool' not in sys.modules
"""
    env = os.environ.copy()
    root = Path(__file__).resolve().parents[3]
    env["PYTHONPATH"] = str(root / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr


def test_pool_close_is_idempotent_and_closed_access_is_typed() -> None:
    async def exercise() -> None:
        raw = FakePool()
        pool = PostgresPool(raw)  # type: ignore[arg-type]

        await pool.close()
        await pool.close()

        assert raw.close_calls == 1
        with pytest.raises(DistributedStoreClosedError):
            async with pool.connection():
                pass

    asyncio.run(exercise())


def test_pool_maps_driver_failure_without_secret_text() -> None:
    async def exercise() -> None:
        raw = FakePool(
            failure=FakeDatabaseError("postgresql://user:secret@example"),
        )
        pool = PostgresPool(
            raw,  # type: ignore[arg-type]
            database_errors=(FakeDatabaseError,),
        )

        with pytest.raises(DistributedBackendUnavailableError) as caught:
            async with pool.connection():
                pass

        assert str(caught.value) == "distributed backend is unavailable"
        assert "secret" not in str(caught.value)

    asyncio.run(exercise())


def test_pool_transaction_delegates_exception_to_driver_rollback() -> None:
    class RecordingTransaction:
        def __init__(self) -> None:
            self.exit_type: object = None

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            del exc_value, traceback
            self.exit_type = exc_type

    class TransactionConnection:
        def __init__(self) -> None:
            self.boundary = RecordingTransaction()

        def transaction(self) -> RecordingTransaction:
            return self.boundary

    class ConnectionContext:
        def __init__(self, connection: TransactionConnection) -> None:
            self.connection = connection

        async def __aenter__(self) -> TransactionConnection:
            return self.connection

        async def __aexit__(self, *args: object) -> None:
            return None

    class TransactionPool(FakePool):
        def __init__(self) -> None:
            super().__init__()
            self.raw_connection = TransactionConnection()

        def connection(self) -> ConnectionContext:
            return ConnectionContext(self.raw_connection)

    async def exercise() -> None:
        raw = TransactionPool()
        pool = PostgresPool(raw)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="injected transaction failure"):
            async with pool.transaction():
                raise RuntimeError("injected transaction failure")

        assert raw.raw_connection.boundary.exit_type is RuntimeError

    asyncio.run(exercise())


def test_pool_cancelled_close_remains_retryable() -> None:
    class BlockingPool(FakePool):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            self.started.set()
            await self.release.wait()

    async def exercise() -> None:
        raw = BlockingPool()
        pool = PostgresPool(raw)  # type: ignore[arg-type]
        closing = asyncio.create_task(pool.close())
        await raw.started.wait()

        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing

        raw.release.set()
        await pool.close()
        assert raw.close_calls == 2
        with pytest.raises(DistributedStoreClosedError):
            async with pool.connection():
                pass

    asyncio.run(exercise())
