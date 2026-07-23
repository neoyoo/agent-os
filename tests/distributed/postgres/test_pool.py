from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

from agentos.distributed.errors import (
    DistributedBackendUnavailableError,
    DistributedShutdownTimeoutError,
    DistributedStoreClosedError,
)
from agentos.distributed.postgres._database import PostgresPool


class FakeDatabaseError(Exception):
    pass


class FakeAsyncConnection:
    close_calls = 0

    async def close(self) -> None:
        FakeAsyncConnection.close_calls += 1


class _HealthyConnection:
    async def execute(self, query: str, params: object = ()) -> object:
        assert query == ""
        assert params == ()
        return object()


class _ConnectionContext:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        if self.failure is not None:
            raise self.failure
        return _HealthyConnection()

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


def _install_fake_driver(
    monkeypatch: pytest.MonkeyPatch,
    raw_pool: object,
) -> None:
    psycopg = ModuleType("psycopg")
    psycopg.AsyncConnection = FakeAsyncConnection  # type: ignore[attr-defined]
    psycopg.Error = FakeDatabaseError  # type: ignore[attr-defined]
    rows = ModuleType("psycopg.rows")
    rows.dict_row = object()  # type: ignore[attr-defined]
    psycopg_pool = ModuleType("psycopg_pool")

    def create_pool(**kwargs: object) -> object:
        assert kwargs["kwargs"] == {"row_factory": rows.dict_row, "autocommit": True}
        connection_class = kwargs["connection_class"]
        assert issubclass(connection_class, FakeAsyncConnection)  # type: ignore[arg-type]
        raw_pool.connection_class = connection_class
        return raw_pool

    psycopg_pool.AsyncConnectionPool = create_pool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)
    monkeypatch.setitem(sys.modules, "psycopg_pool", psycopg_pool)


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


def test_pool_connection_timeout_maps_to_backend_unavailable() -> None:
    class BlockingConnectionContext(_ConnectionContext):
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

    class BlockingPool(FakePool):
        def connection(self) -> BlockingConnectionContext:
            return BlockingConnectionContext()

    async def exercise() -> None:
        pool = PostgresPool(
            BlockingPool(),  # type: ignore[arg-type]
            operation_timeout=timedelta(milliseconds=10),
        )

        with pytest.raises(DistributedBackendUnavailableError):
            async with pool.connection():
                pass

    asyncio.run(exercise())


def test_pool_query_cancellation_aborts_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OpeningPool(FakePool):
        async def open(self, *, wait: bool) -> None:
            assert wait is True

    async def exercise() -> None:
        raw = OpeningPool()
        FakeAsyncConnection.close_calls = 0
        _install_fake_driver(monkeypatch, raw)
        pool = await PostgresPool.open(
            "postgresql://db/agentos",
            operation_timeout=timedelta(milliseconds=100),
        )
        connection = raw.connection_class()  # type: ignore[attr-defined]

        with pytest.raises(asyncio.CancelledError):
            await connection._try_cancel(timeout=5)
        await pool.close()

        assert FakeAsyncConnection.close_calls == 1

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

        async def execute(self, query: str, params: object = ()) -> object:
            assert query == ""
            assert params == ()
            return object()

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


def test_pool_close_timeout_uses_shutdown_error_and_remains_retryable() -> None:
    class BlockingOncePool(FakePool):
        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                await asyncio.Event().wait()

    async def exercise() -> None:
        raw = BlockingOncePool()
        pool = PostgresPool(
            raw,  # type: ignore[arg-type]
            operation_timeout=timedelta(milliseconds=10),
        )

        with pytest.raises(DistributedShutdownTimeoutError):
            await pool.close()
        await pool.close()

        assert raw.close_calls == 2

    asyncio.run(exercise())


def test_pool_cancelled_open_finishes_acquisition_and_closes_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingOpenPool(FakePool):
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            super().__init__()
            self.open_started = asyncio.Event()
            self.allow_open = asyncio.Event()
            self.close_started = asyncio.Event()
            self.allow_close = asyncio.Event()
            self.opened = False

        async def open(self, *, wait: bool) -> None:
            assert wait is True
            self.open_started.set()
            await self.allow_open.wait()
            self.opened = True

        async def close(self) -> None:
            self.close_calls += 1
            self.close_started.set()
            await self.allow_close.wait()

    async def exercise() -> None:
        raw = BlockingOpenPool()
        _install_fake_driver(monkeypatch, raw)

        opening = asyncio.create_task(PostgresPool.open("postgresql://db/agentos"))
        await raw.open_started.wait()
        opening.cancel("caller stopped")
        raw.allow_open.set()
        await raw.close_started.wait()

        assert opening.done() is False
        raw.allow_close.set()

        with pytest.raises(asyncio.CancelledError) as caught:
            await opening

        assert caught.value.args == ("caller stopped",)
        assert raw.opened is True
        assert raw.close_calls == 1

    asyncio.run(exercise())


def test_pool_open_timeout_cancels_acquisition_and_closes_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingOpenPool(FakePool):
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            super().__init__()
            self.cancelled = asyncio.Event()

        async def open(self, *, wait: bool) -> None:
            assert wait is True
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()

    async def exercise() -> None:
        raw = BlockingOpenPool()
        _install_fake_driver(monkeypatch, raw)

        with pytest.raises(DistributedBackendUnavailableError):
            await asyncio.wait_for(
                PostgresPool.open(
                    "postgresql://db/agentos",
                    operation_timeout=timedelta(milliseconds=10),
                ),
                timeout=0.2,
            )

        assert raw.cancelled.is_set()
        assert raw.close_calls == 1

    asyncio.run(exercise())
