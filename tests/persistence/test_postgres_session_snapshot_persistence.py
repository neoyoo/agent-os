from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.artifacts import ArtifactRef
from agentos.compression import CompressionIndex
from agentos.context import ContextState
from agentos.messages import MessageRuntime, StoredMessage, ToolCall
from agentos.persistence import (
    PostgresDurableSessionStore,
    PostgresSessionSnapshotPersistence,
    SnapshotConflictError,
    SessionSnapshot,
)
from agentos.persistence.serializers import session_snapshot_to_dict
from agentos.providers.json_values import FrozenJsonObject
from agentos.runtime import SessionState
from agentos.channels import SessionLease, SessionLeaseError


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.messages: dict[tuple[str, str], dict[str, object]] = {}
        self.snapshots: dict[str, dict[str, object]] = {}
        self._pending_snapshots: dict[str, dict[str, object] | None] = {}
        self.sql: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def _lease_fence(self, value: object) -> int:
        return 0 if value is None else int(value)

    def _visible_snapshots(self) -> dict[str, dict[str, object]]:
        visible = dict(self.snapshots)
        for session_id, row in self._pending_snapshots.items():
            if row is None:
                visible.pop(session_id, None)
            else:
                visible[session_id] = row
        return visible

    def _stage_snapshot(self, session_id: str, row: dict[str, object]) -> None:
        self._pending_snapshots[session_id] = row

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        visible_snapshots = self._visible_snapshots()
        if (
            "INSERT INTO agentos_session_snapshots" in sql
            and "DO NOTHING" in sql
        ):
            session_id = str(params[0])
            if session_id in visible_snapshots:
                return FakeCursor()
            payload = json.loads(str(params[2]))
            row = {
                "version": params[1],
                "revision": 1,
                "lease_fence": (
                    self._lease_fence(params[3]) if len(params) > 3 else 0
                ),
                "payload": payload,
            }
            self._stage_snapshot(session_id, row)
            return FakeCursor([(1, row["lease_fence"])])
        if (
            "INSERT INTO agentos_session_snapshots" in sql
            and "DO UPDATE SET" in sql
        ):
            session_id = str(params[0])
            payload = json.loads(str(params[2]))
            revision = visible_snapshots.get(session_id, {}).get("revision", 0) + 1
            current_fence = int(
                visible_snapshots.get(session_id, {}).get("lease_fence", 0),
            )
            lease_fence = self._lease_fence(params[3]) if len(params) > 3 else 0
            row = {
                "version": params[1],
                "revision": revision,
                "lease_fence": max(current_fence, lease_fence),
                "payload": payload,
            }
            self._stage_snapshot(session_id, row)
            return FakeCursor([(revision, row["lease_fence"])])
        if "UPDATE agentos_session_snapshots" in sql:
            lease_fence = self._lease_fence(params[2])
            session_id = str(params[3])
            expected_revision = int(params[4])
            row = visible_snapshots.get(session_id)
            if row is None or int(row["revision"]) != expected_revision:
                return FakeCursor()
            if lease_fence < int(row.get("lease_fence", 0)):
                return FakeCursor()
            payload = json.loads(str(params[1]))
            revision = expected_revision + 1
            updated_row = {
                "version": params[0],
                "revision": revision,
                "lease_fence": lease_fence,
                "payload": payload,
            }
            self._stage_snapshot(session_id, updated_row)
            return FakeCursor([(revision, lease_fence)])
        if "SELECT revision, payload, lease_fence FROM agentos_session_snapshots" in sql:
            row = visible_snapshots.get(str(params[0]))
            return FakeCursor(
                [(row["revision"], row["payload"], row["lease_fence"])] if row else [],
            )
        if "SELECT payload FROM agentos_session_snapshots" in sql:
            row = visible_snapshots.get(str(params[0]))
            return FakeCursor([(row["payload"],)] if row else [])
        if "SELECT session_id FROM agentos_session_snapshots" in sql:
            return FakeCursor(
                [(session_id,) for session_id in sorted(visible_snapshots)],
            )
        if "DELETE FROM agentos_session_snapshots" in sql:
            self._pending_snapshots[str(params[0])] = None
            return FakeCursor()
        if "INSERT INTO agentos_messages" in sql:
            self.messages[(str(params[0]), str(params[1]))] = json.loads(
                str(params[2]),
            )
            return FakeCursor()
        if "SELECT message_id, payload FROM agentos_messages" in sql:
            session_id = str(params[0])
            message_ids = params[1]
            if not isinstance(message_ids, list):
                raise AssertionError("message ids must be a list")
            return FakeCursor(
                [
                    (message_id, self.messages[(session_id, message_id)])
                    for message_id in message_ids
                    if (session_id, message_id) in self.messages
                ],
            )
        return FakeCursor()

    def commit(self) -> None:
        for session_id, row in self._pending_snapshots.items():
            if row is None:
                self.snapshots.pop(session_id, None)
            else:
                self.snapshots[session_id] = row
        self._pending_snapshots.clear()
        self.commits += 1

    def rollback(self) -> None:
        self._pending_snapshots.clear()
        self.rollbacks += 1


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


def make_stored_message(*, tags: tuple[str, ...]) -> StoredMessage:
    return StoredMessage(
        id="msg_1",
        role="assistant",
        content="",
        artifact_refs=(
            ArtifactRef(
                artifact_id="art_drawing",
                filename="drawing.png",
                media_type="image/png",
            ),
        ),
        tool_calls=(
            ToolCall(
                id="call_1",
                name="inspect",
                arguments={"filters": {"tags": list(tags)}},
            ),
        ),
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


def test_postgres_session_snapshot_round_trips_stored_message_artifacts() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )
    messages = MessageRuntime()
    message = make_stored_message(tags=("phase2",))
    messages.hydrate_messages([message])
    messages.active_window.append(message.id)
    snapshot = SessionSnapshot(
        session_state=SessionState(id="session_1"),
        context_state=ContextState(),
        message_runtime=messages,
        compression_index=CompressionIndex(),
    )

    store.save(snapshot)
    restored = store.load("session_1")

    assert restored.message_runtime.store.get("msg_1") == message


def test_postgres_durable_store_uses_canonical_stored_message_serializer() -> None:
    connection = FakeConnection()
    store = PostgresDurableSessionStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    message = make_stored_message(tags=("phase2", "postgres"))

    store.append_message("session_1", message)
    restored = store.get_messages("session_1", [message.id])

    assert restored == [message]
    assert type(restored[0]) is StoredMessage
    arguments = restored[0].tool_calls[0].arguments
    assert isinstance(arguments, FrozenJsonObject)
    filters = arguments["filters"]
    assert isinstance(filters, FrozenJsonObject)
    assert filters["tags"] == ("phase2", "postgres")
    with pytest.raises(TypeError):
        arguments["filters"] = {}

    wire = connection.messages[("session_1", "msg_1")]
    assert set(wire) == {
        "id",
        "role",
        "content",
        "artifact_refs",
        "tool_calls",
        "tool_call_id",
    }
    assert {
        "context_snapshot",
        "origin",
        "authority",
        "persistence",
        "visibility",
    }.isdisjoint(wire)


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


class RecordingLeaseStore:
    def __init__(
        self,
        *,
        owned: bool = True,
        lose_after_first_check: bool = False,
    ) -> None:
        self.owned = owned
        self.lose_after_first_check = lose_after_first_check
        self.ensure_calls: list[SessionLease] = []

    def ensure_owned(self, lease: SessionLease) -> None:
        self.ensure_calls.append(lease)
        if not self.owned:
            raise SessionLeaseError(f"session lease is not owned: {lease.session_id}")
        if self.lose_after_first_check and len(self.ensure_calls) == 1:
            self.owned = False


def test_postgres_session_snapshot_persistence_save_if_lease_owned_fences_write() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )
    lease_store = RecordingLeaseStore()
    lease = SessionLease(
        session_id="session_1",
        owner_id="node-a",
        token="lease-token",
        fence=7,
    )

    record = store.save_if_lease_owned(
        make_snapshot(),
        expected_revision=0,
        lease=lease,
        lease_store=lease_store,
    )

    assert record.revision == 1
    assert lease_store.ensure_calls == [lease, lease]
    assert connection.commits == 1
    assert connection.snapshots["session_1"]["lease_fence"] == 7


def test_postgres_session_snapshot_persistence_save_if_lease_owned_rejects_stale_lease() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )
    lease_store = RecordingLeaseStore(owned=False)

    with pytest.raises(SessionLeaseError, match="session lease is not owned"):
        store.save_if_lease_owned(
            make_snapshot(),
            expected_revision=0,
            lease=SessionLease(
                session_id="session_1",
                owner_id="node-a",
                token="stale-token",
            ),
            lease_store=lease_store,
        )

    assert connection.snapshots == {}
    assert connection.commits == 0


def test_postgres_session_snapshot_persistence_save_if_lease_owned_rechecks_after_save() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )
    lease_store = RecordingLeaseStore(lose_after_first_check=True)
    lease = SessionLease(
        session_id="session_1",
        owner_id="node-a",
        token="lease-token",
        fence=7,
    )

    with pytest.raises(SessionLeaseError, match="session lease is not owned"):
        store.save_if_lease_owned(
            make_snapshot(),
            expected_revision=0,
            lease=lease,
            lease_store=lease_store,
        )

    assert lease_store.ensure_calls == [lease, lease]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.snapshots == {}


def test_postgres_session_snapshot_persistence_save_if_lease_owned_rejects_wrong_session_lease() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )
    lease_store = RecordingLeaseStore()

    with pytest.raises(SessionLeaseError, match="session lease mismatch"):
        store.save_if_lease_owned(
            make_snapshot("session_1"),
            expected_revision=0,
            lease=SessionLease(
                session_id="session_2",
                owner_id="node-a",
                token="lease-token",
            ),
            lease_store=lease_store,
        )

    assert lease_store.ensure_calls == []
    assert connection.snapshots == {}
    assert connection.commits == 0


def test_postgres_session_snapshot_persistence_save_if_lease_owned_rejects_older_fence() -> None:
    connection = FakeConnection()
    store = PostgresSessionSnapshotPersistence(
        dsn="postgresql://unused",
        connection=connection,
    )
    lease_store = RecordingLeaseStore()
    first = store.save_if_lease_owned(
        make_snapshot("session_1"),
        expected_revision=0,
        lease=SessionLease(
            session_id="session_1",
            owner_id="node-b",
            token="new-token",
            fence=4,
        ),
        lease_store=lease_store,
    )

    with pytest.raises(SnapshotConflictError, match="snapshot revision conflict"):
        store.save_if_lease_owned(
            make_snapshot("session_1"),
            expected_revision=first.revision,
            lease=SessionLease(
                session_id="session_1",
                owner_id="node-a",
                token="old-token",
                fence=3,
            ),
            lease_store=lease_store,
        )

    assert connection.snapshots["session_1"]["lease_fence"] == 4


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
    assert "lease_fence BIGINT NOT NULL" in migration
    assert "session_id TEXT PRIMARY KEY" in migration
