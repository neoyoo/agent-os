import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentos.memory import (
    InMemoryMemoryStore,
    MemoryRecord,
    MemorySelectionContext,
)
from agentos.memory.sqlite import (
    SQLiteMemoryStore,
    SQLiteMemoryStoreClosedError,
    SQLiteMemoryStoreCorruptedError,
)


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def record(
    handle: str,
    *,
    session_id: str = "session_1",
    content: str = "Python project preference.",
    artifact_handles: tuple[str, ...] = (),
    expires_at: datetime | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        handle=handle,
        session_id=session_id,
        kind="semantic",
        category="preference",
        content=content,
        artifact_handles=artifact_handles,
        expires_at=expires_at,
    )


def selection_context(
    *,
    session_id: str = "session_1",
    query: str = "project python",
) -> MemorySelectionContext:
    return MemorySelectionContext(
        session_id=session_id,
        principal_id="user_1",
        permissions={"memory:read"},
        query=query,
        now=NOW,
    )


def test_sqlite_store_persists_replacement_across_restart(tmp_path) -> None:
    database_path = tmp_path / "state" / "memory.db"
    replacement = record("mem_1", content="Current value")

    with SQLiteMemoryStore(database_path) as store:
        store.put(record("mem_1", content="First value"))
        store.put(replacement)

    with SQLiteMemoryStore(database_path) as restarted:
        assert restarted.get("mem_1") == replacement
        with pytest.raises(KeyError, match="missing"):
            restarted.get("missing")


def test_sqlite_store_rejects_cross_session_handle_after_restart(tmp_path) -> None:
    database_path = tmp_path / "memory.db"
    original = record("mem_1")
    with SQLiteMemoryStore(database_path) as store:
        store.put(original)

    with SQLiteMemoryStore(database_path) as restarted:
        with pytest.raises(
            ValueError,
            match="memory handle already belongs to another session",
        ):
            restarted.put(record("mem_1", session_id="session_2"))
        assert restarted.get("mem_1") == original


def test_sqlite_store_search_matches_in_memory_scoring_and_scope(tmp_path) -> None:
    sqlite_store = SQLiteMemoryStore(tmp_path / "memory.db")
    memory_store = InMemoryMemoryStore()
    records = (
        record("mem_b", content="Python project"),
        record("mem_a", content="Python project"),
        record("mem_low", content="Python only"),
        record("mem_none", content="Rust workspace"),
        record("mem_other", session_id="session_2"),
    )
    try:
        for memory_record in records:
            sqlite_store.put(memory_record)
            memory_store.put(memory_record)

        context = selection_context()
        assert sqlite_store.search(context, 10) == memory_store.search(context, 10)
        empty = selection_context(query="")
        assert sqlite_store.search(empty, 2) == memory_store.search(empty, 2)
    finally:
        sqlite_store.close()


@pytest.mark.parametrize("candidate_limit", [-1, True, 1.5])
def test_sqlite_store_rejects_invalid_candidate_limit(
    tmp_path,
    candidate_limit: object,
) -> None:
    with SQLiteMemoryStore(tmp_path / "memory.db") as store:
        with pytest.raises(
            ValueError,
            match="candidate_limit must be a non-negative integer",
        ):
            store.search(
                selection_context(),
                candidate_limit,  # type: ignore[arg-type]
            )


def test_sqlite_store_round_trips_artifacts_unicode_and_expiry(tmp_path) -> None:
    expires_at = datetime(
        2026,
        8,
        1,
        9,
        30,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    expected = record(
        "mem_unicode",
        content="用户偏好使用中文。",
        artifact_handles=("art_1", "art_2"),
        expires_at=expires_at,
    )

    with SQLiteMemoryStore(tmp_path / "memory.db") as store:
        store.put(expected)

    with SQLiteMemoryStore(tmp_path / "memory.db") as restarted:
        actual = restarted.get(expected.handle)

    assert actual == expected
    assert actual.expires_at is not None
    assert actual.expires_at.isoformat() == expires_at.isoformat()


def test_sqlite_store_fails_closed_on_corrupted_record(tmp_path) -> None:
    database_path = tmp_path / "memory.db"
    with SQLiteMemoryStore(database_path) as store:
        store.put(record("mem_1"))

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE agentos_memory_records "
            "SET artifact_handles_json = '{' WHERE handle = 'mem_1'",
        )

    with SQLiteMemoryStore(database_path) as restarted:
        with pytest.raises(
            SQLiteMemoryStoreCorruptedError,
            match="^stored memory record is corrupted$",
        ):
            restarted.get("mem_1")
        with pytest.raises(SQLiteMemoryStoreCorruptedError):
            restarted.search(selection_context(), 10)


def test_sqlite_store_close_is_idempotent_and_final(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.close()
    store.close()

    with pytest.raises(SQLiteMemoryStoreClosedError, match="SQLiteMemoryStore is closed"):
        store.put(record("mem_1"))
    with pytest.raises(SQLiteMemoryStoreClosedError):
        store.get("mem_1")
    with pytest.raises(SQLiteMemoryStoreClosedError):
        store.search(selection_context(), 1)
    with pytest.raises(SQLiteMemoryStoreClosedError):
        store.__enter__()


@pytest.mark.parametrize("handle", [None, "", "  "])
def test_sqlite_store_get_rejects_invalid_handle(tmp_path, handle: object) -> None:
    with SQLiteMemoryStore(tmp_path / "memory.db") as store:
        with pytest.raises(ValueError, match="handle must be a non-empty string"):
            store.get(handle)  # type: ignore[arg-type]


def test_sqlite_store_rejects_malformed_schema_at_open(tmp_path) -> None:
    database_path = tmp_path / "memory.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE agentos_memory_records (handle TEXT PRIMARY KEY)"
        )

    with pytest.raises(
        SQLiteMemoryStoreCorruptedError,
        match="^memory store schema is corrupted$",
    ):
        SQLiteMemoryStore(database_path)


def test_sqlite_store_rejects_unsupported_component_version(tmp_path) -> None:
    database_path = tmp_path / "memory.db"
    SQLiteMemoryStore(database_path).close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE agentos_memory_schema SET version = 2")

    with pytest.raises(
        SQLiteMemoryStoreCorruptedError,
        match="^memory store schema is unsupported$",
    ):
        SQLiteMemoryStore(database_path)
