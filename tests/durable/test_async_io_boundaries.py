from __future__ import annotations

import asyncio
import inspect

import aiosqlite
import pytest

from agentos import AgentBuilder
from agentos._sqlite_async import sqlite_transaction
from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.runtime import ArtifactRuntime
from agentos.durable import DurableRuntimeProfile, SQLiteDurableStore
from agentos.durable import sqlite_store as sqlite_store_module
from agentos.providers import FakeProvider
from agentos.runtime.errors import DurableStoreClosedError
from tests.durable._fixtures import checkpoint_source


def test_durable_store_and_profile_have_one_async_lifecycle() -> None:
    assert inspect.iscoroutinefunction(SQLiteDurableStore.open)
    assert inspect.iscoroutinefunction(SQLiteDurableStore.close)
    assert inspect.iscoroutinefunction(DurableRuntimeProfile.open)
    assert inspect.iscoroutinefunction(DurableRuntimeProfile.build_agent)
    assert inspect.iscoroutinefunction(DurableRuntimeProfile.close)
    assert hasattr(DurableRuntimeProfile, "__aenter__")
    assert hasattr(DurableRuntimeProfile, "__aexit__")
    assert not hasattr(DurableRuntimeProfile, "__enter__")
    assert not hasattr(DurableRuntimeProfile, "__exit__")


def test_profile_constructor_performs_no_filesystem_or_database_io(tmp_path) -> None:
    database_path = tmp_path / "state" / "agentos.db"
    artifact_root = tmp_path / "artifacts"

    profile = DurableRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider([])),
        database_path=database_path,
        artifact_root=artifact_root,
    )

    assert not database_path.parent.exists()
    assert not artifact_root.exists()
    assert profile.is_open is False


def test_artifact_store_and_runtime_io_methods_are_async() -> None:
    store_methods = ("put", "get", "read", "list", "delete", "delete_session")
    runtime_methods = (
        "upload",
        "list",
        "read",
        "load_attachment",
        "mount_user_upload",
        "prepare_user_uploads",
        "resolve_mount",
        "delete",
        "delete_session",
        "prepare_projection_cache",
    )

    for method_name in store_methods:
        assert inspect.iscoroutinefunction(
            getattr(InMemoryArtifactStore, method_name),
        )
    for method_name in runtime_methods:
        assert inspect.iscoroutinefunction(getattr(ArtifactRuntime, method_name))


def test_profile_async_context_opens_and_closes_owned_stores(tmp_path) -> None:
    async def scenario() -> None:
        profile = DurableRuntimeProfile(
            agent_builder=AgentBuilder().provider(FakeProvider([])),
            database_path=tmp_path / "state.db",
            artifact_root=tmp_path / "artifacts",
        )
        async with profile as opened:
            assert opened is profile
            assert profile.is_open is True
            await profile.build_agent("session_1")
        assert profile.is_open is False
        await profile.close()

    asyncio.run(scenario())


def test_sqlite_write_wait_does_not_block_event_loop(tmp_path) -> None:
    async def scenario() -> None:
        path = tmp_path / "state.db"
        first = await SQLiteDurableStore.open(path)
        second = await SQLiteDurableStore.open(path)
        writer_started = asyncio.Event()
        writer: asyncio.Task[None] | None = None
        try:
            await first._connection.execute("BEGIN EXCLUSIVE")

            async def write() -> None:
                writer_started.set()
                await second.initialize_session(checkpoint_source().session)

            writer = asyncio.create_task(write())
            await writer_started.wait()
            assert not writer.done()
            await first._connection.rollback()
            await writer
        finally:
            if writer is not None and not writer.done():
                writer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await writer
            await second.close()
            await first.close()

    asyncio.run(scenario())


def test_cancelled_store_open_closes_partial_connection(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        connections: list[aiosqlite.Connection] = []
        schema_entered = asyncio.Event()
        original_connect = sqlite_store_module.aiosqlite.connect

        async def capture_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
            connection = await original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        async def blocked_schema(_connection) -> None:  # type: ignore[no-untyped-def]
            schema_entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(sqlite_store_module.aiosqlite, "connect", capture_connect)
        monkeypatch.setattr(
            sqlite_store_module,
            "initialize_durable_schema",
            blocked_schema,
        )

        opening = asyncio.create_task(
            SQLiteDurableStore.open(tmp_path / "cancelled.db")
        )
        await schema_entered.wait()
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening

        assert len(connections) == 1
        with pytest.raises(ValueError, match="^no active connection$"):
            await connections[0].execute("SELECT 1")

    asyncio.run(scenario())


def test_cancelled_store_close_marks_completed_close_atomically(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        store = await SQLiteDurableStore.open(tmp_path / "state.db")
        connection = store._connection
        assert connection is not None
        close_completed = asyncio.Event()
        release_close = asyncio.Event()
        original_close = connection.close

        async def blocked_close() -> None:
            await original_close()
            close_completed.set()
            await release_close.wait()

        monkeypatch.setattr(connection, "close", blocked_close)
        closing = asyncio.create_task(store.close())
        await asyncio.wait_for(close_completed.wait(), timeout=5)
        closing.cancel("cancel completed close")
        release_close.set()

        with pytest.raises(asyncio.CancelledError) as caught:
            await closing
        assert caught.value.args == ("cancel completed close",)
        assert store._connection is None
        with pytest.raises(DurableStoreClosedError):
            store._ensure_open()
        await store.close()

    asyncio.run(scenario())


def test_cancelled_sqlite_begin_is_rolled_back_before_propagation() -> None:
    class ControlledConnection:
        def __init__(self) -> None:
            self.begin_started = asyncio.Event()
            self.release_begin = asyncio.Event()
            self.in_transaction = False
            self.rollback_calls = 0

        async def execute(self, sql: str) -> None:
            assert sql == "BEGIN IMMEDIATE"
            self.begin_started.set()
            await self.release_begin.wait()
            self.in_transaction = True

        async def commit(self) -> None:
            self.in_transaction = False

        async def rollback(self) -> None:
            self.rollback_calls += 1
            self.in_transaction = False

    async def scenario() -> None:
        connection = ControlledConnection()

        async def transact() -> None:
            async with sqlite_transaction(connection):  # type: ignore[arg-type]
                pytest.fail("cancelled begin must not enter the transaction body")

        task = asyncio.create_task(transact())
        await connection.begin_started.wait()
        task.cancel()
        connection.release_begin.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert connection.in_transaction is False
        assert connection.rollback_calls == 1

    asyncio.run(scenario())


def test_cancelled_sqlite_commit_reports_commit_without_rollback() -> None:
    class ControlledConnection:
        def __init__(self) -> None:
            self.commit_started = asyncio.Event()
            self.release_commit = asyncio.Event()
            self.in_transaction = False
            self.rollback_calls = 0

        async def execute(self, sql: str) -> None:
            assert sql == "BEGIN IMMEDIATE"
            self.in_transaction = True

        async def commit(self) -> None:
            self.commit_started.set()
            await self.release_commit.wait()
            self.in_transaction = False

        async def rollback(self) -> None:
            self.rollback_calls += 1
            self.in_transaction = False

    async def scenario() -> None:
        connection = ControlledConnection()
        committed = False

        def mark_committed() -> None:
            nonlocal committed
            committed = True

        async def transact() -> None:
            async with sqlite_transaction(  # type: ignore[arg-type]
                connection,
                on_commit=mark_committed,
            ):
                pass

        task = asyncio.create_task(transact())
        await asyncio.wait_for(connection.commit_started.wait(), timeout=5)
        task.cancel("cancel after commit started")
        connection.release_commit.set()

        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("cancel after commit started",)
        assert committed is True
        assert connection.in_transaction is False
        assert connection.rollback_calls == 0

    asyncio.run(scenario())
