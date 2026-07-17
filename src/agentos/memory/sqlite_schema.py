from __future__ import annotations

import sqlite3

from agentos.memory.sqlite_errors import SQLiteMemoryStoreCorruptedError


SCHEMA_VERSION = 1
_EXPECTED_COLUMNS = (
    ("handle", "TEXT", 1, 1),
    ("session_id", "TEXT", 1, 0),
    ("kind", "TEXT", 1, 0),
    ("category", "TEXT", 1, 0),
    ("content", "TEXT", 1, 0),
    ("artifact_handles_json", "TEXT", 1, 0),
    ("expires_at", "TEXT", 0, 0),
)


def initialize_memory_schema(connection: sqlite3.Connection) -> None:
    """初始化并严格验证 Memory Adapter 自己的 schema。"""

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS agentos_memory_schema ("
            "version INTEGER PRIMARY KEY)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agentos_memory_records (
                handle TEXT PRIMARY KEY NOT NULL,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                artifact_handles_json TEXT NOT NULL,
                expires_at TEXT
            ) WITHOUT ROWID
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS agentos_memory_records_session_idx "
            "ON agentos_memory_records (session_id, handle)"
        )
        _validate_structure(connection)
        versions = connection.execute(
            "SELECT version FROM agentos_memory_schema"
        ).fetchall()
        if not versions:
            connection.execute(
                "INSERT INTO agentos_memory_schema (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
            raise SQLiteMemoryStoreCorruptedError(
                "memory store schema is unsupported"
            )
        connection.commit()
    except SQLiteMemoryStoreCorruptedError:
        connection.rollback()
        raise
    except (sqlite3.DatabaseError, TypeError, ValueError):
        connection.rollback()
        raise SQLiteMemoryStoreCorruptedError(
            "memory store schema is corrupted"
        ) from None


def _validate_structure(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA table_xinfo(agentos_memory_records)").fetchall()
    actual = tuple((row[1], row[2].upper(), row[3], row[5]) for row in rows)
    indexes = connection.execute("PRAGMA index_list(agentos_memory_records)").fetchall()
    session_index = False
    for index in indexes:
        columns = connection.execute(
            f'PRAGMA index_info("{str(index[1]).replace(chr(34), chr(34) * 2)}")'
        ).fetchall()
        if tuple(row[2] for row in columns) == ("session_id", "handle"):
            session_index = True
            break
    if actual != _EXPECTED_COLUMNS or not session_index:
        raise SQLiteMemoryStoreCorruptedError("memory store schema is corrupted")


__all__ = ["initialize_memory_schema"]
