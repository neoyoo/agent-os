from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.compression import CompressionIndex
from agentos.context import ContextState
from agentos.messages import MessageRuntime
from agentos.persistence import (
    PostgresSessionSnapshotPersistence,
    SnapshotConflictError,
    SessionSnapshot,
)
from agentos.persistence.serializers import session_snapshot_to_dict
from agentos.runtime import SessionState


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, object]] = {}
        self.sql: list[str] = []
        self.commits = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if (
            "INSERT INTO agentos_session_snapshots" in sql
            and "DO NOTHING" in sql
        ):
            session_id = str(params[0])
            if session_id in self.snapshots:
                return FakeCursor()
            payload = json.loads(str(params[2]))
            self.snapshots[session_id] = {
                "version": params[1],
                "revision": 1,
                "payload": payload,
            }
            return FakeCursor([(1,)])
        if (
            "INSERT INTO agentos_session_snapshots" in sql
            and "DO UPDATE SET" in sql
        ):
            session_id = str(params[0])
            payload = json.loads(str(params[2]))
            revision = self.snapshots.get(session_id, {}).get("revision", 0) + 1
            self.snapshots[session_id] = {
                "version": params[1],
                "revision": revision,
                "payload": payload,
            }
            return FakeCursor([(revision,)])
        if "UPDATE agentos_session_snapshots" in sql:
            session_id = str(params[2])
            expected_revision = int(params[3])
            row = self.snapshots.get(session_id)
            if row is None or int(row["revision"]) != expected_revision:
                return FakeCursor()
            payload = json.loads(str(params[1]))
            revision = expected_revision + 1
            self.snapshots[session_id] = {
                "version": params[0],
                "revision": revision,
                "payload": payload,
            }
            return FakeCursor([(revision,)])
        if "SELECT revision, payload FROM agentos_session_snapshots" in sql:
            row = self.snapshots.get(str(params[0]))
            return FakeCursor([(row["revision"], row["payload"])] if row else [])
        if "SELECT payload FROM agentos_session_snapshots" in sql:
            row = self.snapshots.get(str(params[0]))
            return FakeCursor([(row["payload"],)] if row else [])
        if "SELECT session_id FROM agentos_session_snapshots" in sql:
            return FakeCursor([(session_id,) for session_id in sorted(self.snapshots)])
        if "DELETE FROM agentos_session_snapshots" in sql:
            self.snapshots.pop(str(params[0]), None)
            return FakeCursor()
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.gets = 0
        self.puts: list[FakeConnection] = []

    def getconn(self) -> FakeConnection:
        self.gets += 1
        return self.connection

    def putconn(self, connection: FakeConnection) -> None:
        self.puts.append(connection)


class DistinctConnectionPool:
    def __init__(self) -> None:
        self.connections = [FakeConnection(), FakeConnection()]
        self.gets = 0
        self.puts: list[FakeConnection] = []

    def getconn(self) -> FakeConnection:
        connection = self.connections[self.gets]
        self.gets += 1
        return connection

    def putconn(self, connection: FakeConnection) -> None:
        self.puts.append(connection)


def make_snapshot(session_id: str = "session_1") -> SessionSnapshot:
    messages = MessageRuntime()
    messages.append_user("hello")
    return SessionSnapshot(
        session_state=SessionState(id=session_id),
        context_state=ContextState(working_state={"task_goal": "Persist."}),
        message_runtime=messages,
        compression_index=CompressionIndex(),
    )


def test_postgres_session_snapshot_persistence_round_trips_snapshot() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )

    store.save(make_snapshot())
    restored = store.load("session_1")

    assert restored.session_state.id == "session_1"
    assert restored.message_runtime.store.get("msg_1").content == "hello"
    assert session_snapshot_to_dict(restored) == session_snapshot_to_dict(
        make_snapshot(),
    )
    assert connection.commits == 1
    joined_sql = "\n".join(connection.sql)
    assert "ON CONFLICT (session_id) DO UPDATE" in joined_sql
    assert "payload = EXCLUDED.payload" in joined_sql


def test_postgres_session_snapshot_persistence_from_pool_borrows_per_method() -> None:
    connection = FakeConnection()
    pool = FakePool(connection)
    store = PostgresSessionSnapshotPersistence.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    assert pool.gets == 0

    store.save(make_snapshot())
    assert pool.gets == 1
    assert pool.puts == [connection]

    restored = store.load("session_1")
    assert restored.session_state.id == "session_1"
    assert pool.gets == 2
    assert pool.puts == [connection, connection]

    store.close()
    assert pool.puts == [connection, connection]


def test_postgres_session_snapshot_persistence_pool_scopes_do_not_share_active_connection() -> None:
    pool = DistinctConnectionPool()
    store = PostgresSessionSnapshotPersistence.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    with store._connection_scope() as first:
        with store._connection_scope() as second:
            assert second is not first

    assert pool.gets == 2
    assert pool.puts == [pool.connections[1], pool.connections[0]]


def test_postgres_session_snapshot_persistence_loads_revision_record() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )

    saved = store.save_if_unchanged(make_snapshot(), expected_revision=0)
    loaded = store.load_record("session_1")

    assert saved.revision == 1
    assert loaded.revision == 1
    assert session_snapshot_to_dict(loaded.snapshot) == session_snapshot_to_dict(
        make_snapshot(),
    )


def test_postgres_session_snapshot_persistence_rejects_stale_revision() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )

    first = store.save_if_unchanged(make_snapshot(), expected_revision=0)
    second = store.save_if_unchanged(
        make_snapshot("session_1"),
        expected_revision=first.revision,
    )

    with pytest.raises(SnapshotConflictError, match="snapshot revision conflict"):
        store.save_if_unchanged(
            make_snapshot("session_1"),
            expected_revision=first.revision,
        )

    assert second.revision == 2


def test_postgres_session_snapshot_persistence_lists_and_deletes() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )
    store.save(make_snapshot("b"))
    store.save(make_snapshot("a"))

    assert store.list_ids() == ["a", "b"]

    store.delete("a")

    assert store.list_ids() == ["b"]
    assert connection.commits == 3


def test_postgres_session_snapshot_persistence_missing_session_raises_key_error() -> None:
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=FakeConnection(),
    )

    try:
        store.load("missing")
    except KeyError as error:
        assert error.args == ("missing",)
    else:
        raise AssertionError("expected KeyError")


def test_postgres_session_snapshot_migration_defines_jsonb_table() -> None:
    migration = Path(
        "docs/migrations/2026-06-12-postgres-session-snapshots.sql",
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS agentos_session_snapshots" in migration
    assert "payload JSONB NOT NULL" in migration
    assert "revision BIGINT NOT NULL" in migration
    assert "session_id TEXT PRIMARY KEY" in migration
