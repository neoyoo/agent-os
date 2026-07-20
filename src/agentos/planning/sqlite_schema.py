from __future__ import annotations

import aiosqlite

from agentos.planning.sqlite_errors import SQLitePlanStoreCorruptedError


SCHEMA_VERSION = 1
CREATE_PLANS_TABLE = """
CREATE TABLE IF NOT EXISTS agentos_plans (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL UNIQUE,
    owner_agent_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    payload_json TEXT NOT NULL
)
"""
_EXPECTED_COLUMNS = (
    ("sequence", "INTEGER", 0, 1),
    ("plan_id", "TEXT", 1, 0),
    ("owner_agent_id", "TEXT", 1, 0),
    ("revision", "INTEGER", 1, 0),
    ("schema_version", "INTEGER", 1, 0),
    ("payload_json", "TEXT", 1, 0),
)


async def initialize_plan_schema(connection: aiosqlite.Connection) -> None:
    """初始化并严格验证 Plan Adapter 自己的 schema。"""

    try:
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS agentos_plan_schema ("
            "version INTEGER PRIMARY KEY)"
        )
        await connection.execute(CREATE_PLANS_TABLE)
        await _validate_columns(connection)
        await _validate_unique_plan_id(connection)
        async with connection.execute(
            "SELECT version FROM agentos_plan_schema"
        ) as cursor:
            versions = await cursor.fetchall()
        if not versions:
            await connection.execute(
                "INSERT INTO agentos_plan_schema (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
            raise SQLitePlanStoreCorruptedError("plan store schema is unsupported")
        await connection.commit()
    except SQLitePlanStoreCorruptedError:
        await connection.rollback()
        raise
    except (aiosqlite.DatabaseError, TypeError, ValueError):
        await connection.rollback()
        raise SQLitePlanStoreCorruptedError("plan store schema is corrupted") from None


async def _validate_columns(connection: aiosqlite.Connection) -> None:
    async with connection.execute("PRAGMA table_xinfo(agentos_plans)") as cursor:
        rows = await cursor.fetchall()
    actual = tuple((row[1], row[2].upper(), row[3], row[5]) for row in rows)
    if actual != _EXPECTED_COLUMNS:
        raise SQLitePlanStoreCorruptedError("plan store schema is corrupted")


async def _validate_unique_plan_id(connection: aiosqlite.Connection) -> None:
    async with connection.execute("PRAGMA index_list(agentos_plans)") as cursor:
        indexes = await cursor.fetchall()
    for index in indexes:
        if index[2] != 1:
            continue
        async with connection.execute(
            f'PRAGMA index_info("{str(index[1]).replace(chr(34), chr(34) * 2)}")'
        ) as cursor:
            columns = await cursor.fetchall()
        if tuple(row[2] for row in columns) == ("plan_id",):
            return
    raise SQLitePlanStoreCorruptedError("plan store schema is corrupted")


__all__ = ["SCHEMA_VERSION", "initialize_plan_schema"]
