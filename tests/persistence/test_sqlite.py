from pathlib import Path
import sqlite3

from agentos.compression import CompressionIndex
from agentos.context import ContextState
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.persistence import SQLitePersistence, SessionSnapshot, SnapshotLoadError
from agentos.runtime import SessionState, TurnStartedEvent


def make_snapshot(session_id: str = "session_1") -> SessionSnapshot:
    messages = MessageRuntime()
    messages.append_user("hello")
    event_log = EventLog()
    event_log.record(
        TurnStartedEvent(
            session_id=session_id,
            turn_id="turn_1",
            user_input="hello",
        ),
    )
    return SessionSnapshot(
        session_state=SessionState(id=session_id),
        context_state=ContextState(),
        message_runtime=messages,
        compression_index=CompressionIndex(),
        event_records=tuple(event_log.records),
    )


def test_sqlite_persistence_round_trips_latest_snapshot_and_events(
    tmp_path: Path,
) -> None:
    store = SQLitePersistence(tmp_path / "sessions.sqlite3")
    store.save(make_snapshot())

    restored = store.load("session_1")

    assert restored.message_runtime.store.get("msg_1").content == "hello"
    assert restored.event_records[0].event_type == "TurnStartedEvent"
    assert store.list_ids() == ["session_1"]


def test_sqlite_persistence_delete_removes_snapshot(tmp_path: Path) -> None:
    store = SQLitePersistence(tmp_path / "sessions.sqlite3")
    store.save(make_snapshot())
    store.delete("session_1")

    assert store.list_ids() == []


def test_sqlite_persistence_missing_session_raises_key_error(tmp_path: Path) -> None:
    store = SQLitePersistence(tmp_path / "sessions.sqlite3")

    try:
        store.load("missing")
    except KeyError as error:
        assert error.args == ("missing",)
    else:
        raise AssertionError("missing session should raise KeyError")


def test_sqlite_persistence_corrupt_json_raises_snapshot_load_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions.sqlite3"
    store = SQLitePersistence(path)
    store.save(make_snapshot())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE snapshots SET payload_json = ? WHERE session_id = ?",
            ("{not json", "session_1"),
        )

    try:
        store.load("session_1")
    except SnapshotLoadError as error:
        assert "session_1" in str(error)
    else:
        raise AssertionError("corrupt snapshot should raise SnapshotLoadError")


def test_sqlite_persistence_uses_wal_journal_mode(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    SQLitePersistence(path)

    with sqlite3.connect(path) as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode.lower() == "wal"
