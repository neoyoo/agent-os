from __future__ import annotations

import aiosqlite

from agentos.capabilities.skill_activation_errors import (
    SkillActivationCorruptedError,
)


SCHEMA_VERSION = 1
_CREATE_ACTIVATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS agentos_skill_activations (
    session_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    PRIMARY KEY (session_id, skill_name)
) WITHOUT ROWID
"""
_EXPECTED_SCHEMA_COLUMNS = (("version", "INTEGER", 0, 1),)
_EXPECTED_ACTIVATION_COLUMNS = (
    ("session_id", "TEXT", 1, 1),
    ("skill_name", "TEXT", 1, 2),
    ("source_id", "TEXT", 1, 0),
    ("source_revision", "TEXT", 1, 0),
    ("content_digest", "TEXT", 1, 0),
    ("policy_id", "TEXT", 1, 0),
)
_EXPECTED_UNIQUE_INDEXES = {("session_id", "skill_name")}


async def initialize_skill_activation_schema(connection: aiosqlite.Connection) -> None:
    """初始化并严格验证 Skill Activation Adapter 自己的 schema。"""

    try:
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS agentos_skill_activation_schema ("
            "version INTEGER PRIMARY KEY)"
        )
        await connection.execute(_CREATE_ACTIVATIONS_TABLE)
        await _validate_columns(connection)
        await _validate_unique_indexes(connection)
        async with connection.execute(
            "SELECT version FROM agentos_skill_activation_schema"
        ) as cursor:
            versions = await cursor.fetchall()
        if not versions:
            await connection.execute(
                "INSERT INTO agentos_skill_activation_schema (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
            raise SkillActivationCorruptedError(
                "skill activation store schema is unsupported"
            )
        await connection.commit()
    except SkillActivationCorruptedError:
        await connection.rollback()
        raise
    except (aiosqlite.DatabaseError, TypeError, ValueError):
        await connection.rollback()
        raise SkillActivationCorruptedError(
            "skill activation store schema is corrupted"
        ) from None


async def _validate_columns(connection: aiosqlite.Connection) -> None:
    async with connection.execute(
        "PRAGMA table_xinfo(agentos_skill_activation_schema)"
    ) as cursor:
        schema_rows = await cursor.fetchall()
    async with connection.execute(
        "PRAGMA table_xinfo(agentos_skill_activations)"
    ) as cursor:
        activation_rows = await cursor.fetchall()
    schema_columns = tuple(
        (row[1], row[2].upper(), row[3], row[5]) for row in schema_rows
    )
    activation_columns = tuple(
        (row[1], row[2].upper(), row[3], row[5]) for row in activation_rows
    )
    if (
        schema_columns != _EXPECTED_SCHEMA_COLUMNS
        or activation_columns != _EXPECTED_ACTIVATION_COLUMNS
    ):
        raise SkillActivationCorruptedError(
            "skill activation store schema is corrupted"
        )


async def _validate_unique_indexes(connection: aiosqlite.Connection) -> None:
    unique_indexes = set()
    async with connection.execute(
        "PRAGMA index_list(agentos_skill_activations)"
    ) as cursor:
        indexes = await cursor.fetchall()
    for index in indexes:
        if index[2] != 1:
            continue
        escaped_name = str(index[1]).replace('"', '""')
        async with connection.execute(
            f'PRAGMA index_info("{escaped_name}")'
        ) as cursor:
            columns = await cursor.fetchall()
        unique_indexes.add(tuple(row[2] for row in columns))
    if unique_indexes != _EXPECTED_UNIQUE_INDEXES:
        raise SkillActivationCorruptedError(
            "skill activation store schema is corrupted"
        )


__all__ = ["SCHEMA_VERSION", "initialize_skill_activation_schema"]
