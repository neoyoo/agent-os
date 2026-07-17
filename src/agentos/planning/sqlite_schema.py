from __future__ import annotations

import sqlite3

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


def initialize_plan_schema(connection: sqlite3.Connection) -> None:
    """初始化并严格验证 Plan Adapter 自己的 schema。"""

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS agentos_plan_schema ("
            "version INTEGER PRIMARY KEY)"
        )
        connection.execute(CREATE_PLANS_TABLE)
        _validate_columns(connection)
        _validate_unique_plan_id(connection)
        versions = connection.execute(
            "SELECT version FROM agentos_plan_schema"
        ).fetchall()
        if not versions:
            connection.execute(
                "INSERT INTO agentos_plan_schema (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
            raise SQLitePlanStoreCorruptedError("plan store schema is unsupported")
        connection.commit()
    except SQLitePlanStoreCorruptedError:
        connection.rollback()
        raise
    except (sqlite3.DatabaseError, TypeError, ValueError):
        connection.rollback()
        raise SQLitePlanStoreCorruptedError("plan store schema is corrupted") from None


def _validate_columns(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA table_xinfo(agentos_plans)").fetchall()
    actual = tuple((row[1], row[2].upper(), row[3], row[5]) for row in rows)
    if actual != _EXPECTED_COLUMNS:
        raise SQLitePlanStoreCorruptedError("plan store schema is corrupted")


def _validate_unique_plan_id(connection: sqlite3.Connection) -> None:
    indexes = connection.execute("PRAGMA index_list(agentos_plans)").fetchall()
    for index in indexes:
        if index[2] != 1:
            continue
        columns = connection.execute(
            f'PRAGMA index_info("{str(index[1]).replace(chr(34), chr(34) * 2)}")'
        ).fetchall()
        if tuple(row[2] for row in columns) == ("plan_id",):
            return
    raise SQLitePlanStoreCorruptedError("plan store schema is corrupted")


__all__ = ["SCHEMA_VERSION", "initialize_plan_schema"]
