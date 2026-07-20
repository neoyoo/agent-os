from __future__ import annotations

from collections.abc import Sequence

import aiosqlite


SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS durable_schema (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS durable_sessions (
    session_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    next_turn_number INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS durable_runs (
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    wait_kind TEXT,
    wait_handle TEXT,
    wait_detail TEXT,
    wait_not_before TEXT,
    aggregate_version INTEGER NOT NULL,
    recovery_error TEXT,
    PRIMARY KEY (session_id, run_id),
    FOREIGN KEY (session_id) REFERENCES durable_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS durable_commands (
    command_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    turn_id TEXT,
    aggregate_version INTEGER NOT NULL,
    FOREIGN KEY (session_id, run_id)
        REFERENCES durable_runs(session_id, run_id)
);

CREATE TABLE IF NOT EXISTS durable_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    FOREIGN KEY (session_id, run_id)
        REFERENCES durable_runs(session_id, run_id)
);

CREATE TABLE IF NOT EXISTS durable_messages (
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (session_id, message_id),
    UNIQUE (session_id, position),
    FOREIGN KEY (session_id) REFERENCES durable_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS durable_active_refs (
    session_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (session_id, position),
    UNIQUE (session_id, message_id),
    FOREIGN KEY (session_id, message_id)
        REFERENCES durable_messages(session_id, message_id)
);

CREATE TABLE IF NOT EXISTS durable_context_states (
    session_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES durable_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS durable_execution_cursors (
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (session_id, run_id),
    FOREIGN KEY (session_id, run_id)
        REFERENCES durable_runs(session_id, run_id)
);
"""


_EXPECTED_COLUMNS = {
    "durable_schema": (("version", "INTEGER", 1, 0),),
    "durable_sessions": (
        ("session_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("next_turn_number", "INTEGER", 1, 0),
    ),
    "durable_runs": (
        ("session_id", "TEXT", 1, 1),
        ("run_id", "TEXT", 1, 2),
        ("status", "TEXT", 1, 0),
        ("wait_kind", "TEXT", 0, 0),
        ("wait_handle", "TEXT", 0, 0),
        ("wait_detail", "TEXT", 0, 0),
        ("wait_not_before", "TEXT", 0, 0),
        ("aggregate_version", "INTEGER", 1, 0),
        ("recovery_error", "TEXT", 0, 0),
    ),
    "durable_commands": (
        ("command_id", "TEXT", 0, 1),
        ("session_id", "TEXT", 1, 0),
        ("run_id", "TEXT", 1, 0),
        ("kind", "TEXT", 1, 0),
        ("payload_json", "TEXT", 1, 0),
        ("turn_id", "TEXT", 0, 0),
        ("aggregate_version", "INTEGER", 1, 0),
    ),
    "durable_checkpoints": (
        ("checkpoint_id", "TEXT", 0, 1),
        ("session_id", "TEXT", 1, 0),
        ("run_id", "TEXT", 1, 0),
        ("turn_id", "TEXT", 1, 0),
        ("aggregate_version", "INTEGER", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("schema_version", "INTEGER", 1, 0),
    ),
    "durable_messages": (
        ("session_id", "TEXT", 1, 1),
        ("message_id", "TEXT", 1, 2),
        ("position", "INTEGER", 1, 0),
        ("payload_json", "TEXT", 1, 0),
    ),
    "durable_active_refs": (
        ("session_id", "TEXT", 1, 1),
        ("position", "INTEGER", 1, 2),
        ("message_id", "TEXT", 1, 0),
    ),
    "durable_context_states": (
        ("session_id", "TEXT", 0, 1),
        ("payload_json", "TEXT", 1, 0),
    ),
    "durable_execution_cursors": (
        ("session_id", "TEXT", 1, 1),
        ("run_id", "TEXT", 1, 2),
        ("payload_json", "TEXT", 1, 0),
    ),
}
_EXPECTED_UNIQUE = {
    "durable_sessions": {("session_id",)},
    "durable_runs": {("session_id", "run_id")},
    "durable_commands": {("command_id",)},
    "durable_checkpoints": {("checkpoint_id",)},
    "durable_messages": {
        ("session_id", "message_id"),
        ("session_id", "position"),
    },
    "durable_active_refs": {
        ("session_id", "position"),
        ("session_id", "message_id"),
    },
    "durable_context_states": {("session_id",)},
    "durable_execution_cursors": {("session_id", "run_id")},
}
_EXPECTED_FOREIGN_TARGETS = {
    "durable_runs": {"durable_sessions"},
    "durable_commands": {"durable_runs"},
    "durable_checkpoints": {"durable_runs"},
    "durable_messages": {"durable_sessions"},
    "durable_active_refs": {"durable_messages"},
    "durable_context_states": {"durable_sessions"},
    "durable_execution_cursors": {"durable_runs"},
}


async def validate_durable_schema(connection: aiosqlite.Connection) -> bool:
    """返回 schema 是否与当前 Durable Adapter 精确匹配。"""

    for table, expected in _EXPECTED_COLUMNS.items():
        rows = await _fetchall(connection, f"PRAGMA table_xinfo({table})")
        actual = tuple((row[1], row[2].upper(), row[3], row[5]) for row in rows)
        if actual != expected:
            return False
    for table, required in _EXPECTED_UNIQUE.items():
        actual = set()
        for index in await _fetchall(connection, f"PRAGMA index_list({table})"):
            if index[2] != 1:
                continue
            rows = await _fetchall(
                connection,
                f'PRAGMA index_info("{index[1]}")',
            )
            actual.add(tuple(row[2] for row in rows))
        if not required.issubset(actual):
            return False
    for table, expected in _EXPECTED_FOREIGN_TARGETS.items():
        targets = {
            row[2]
            for row in await _fetchall(
                connection,
                f"PRAGMA foreign_key_list({table})",
            )
        }
        if targets != expected:
            return False
    return True


async def initialize_durable_schema(connection: aiosqlite.Connection) -> None:
    """初始化 Durable schema，并把底层 SQLite 失败映射为稳定错误。"""

    from agentos.runtime.errors import CheckpointCorruptedError

    try:
        await connection.executescript(SCHEMA_SQL)
        if not await validate_durable_schema(connection):
            raise CheckpointCorruptedError("durable schema is corrupted")
        versions = await _fetchall(connection, "SELECT version FROM durable_schema")
        if not versions:
            await connection.execute(
                "INSERT INTO durable_schema (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif len(versions) != 1 or versions[0]["version"] != SCHEMA_VERSION:
            raise CheckpointCorruptedError("durable schema version is unsupported")
    except CheckpointCorruptedError:
        raise
    except aiosqlite.DatabaseError:
        raise CheckpointCorruptedError("durable schema is corrupted") from None


async def _fetchall(
    connection: aiosqlite.Connection,
    sql: str,
    parameters: Sequence[object] = (),
) -> list[aiosqlite.Row]:
    async with connection.execute(sql, parameters) as cursor:
        return await cursor.fetchall()


__all__ = ["initialize_durable_schema"]
