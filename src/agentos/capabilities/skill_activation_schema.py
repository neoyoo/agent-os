from __future__ import annotations

import sqlite3

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


def initialize_skill_activation_schema(connection: sqlite3.Connection) -> None:
    """初始化并严格验证 Skill Activation Adapter 自己的 schema。"""

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS agentos_skill_activation_schema ("
            "version INTEGER PRIMARY KEY)"
        )
        connection.execute(_CREATE_ACTIVATIONS_TABLE)
        _validate_columns(connection)
        _validate_unique_indexes(connection)
        versions = connection.execute(
            "SELECT version FROM agentos_skill_activation_schema"
        ).fetchall()
        if not versions:
            connection.execute(
                "INSERT INTO agentos_skill_activation_schema (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
            raise SkillActivationCorruptedError(
                "skill activation store schema is unsupported"
            )
        connection.commit()
    except SkillActivationCorruptedError:
        connection.rollback()
        raise
    except (sqlite3.DatabaseError, TypeError, ValueError):
        connection.rollback()
        raise SkillActivationCorruptedError(
            "skill activation store schema is corrupted"
        ) from None


def _validate_columns(connection: sqlite3.Connection) -> None:
    schema_rows = connection.execute(
        "PRAGMA table_xinfo(agentos_skill_activation_schema)"
    ).fetchall()
    activation_rows = connection.execute(
        "PRAGMA table_xinfo(agentos_skill_activations)"
    ).fetchall()
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


def _validate_unique_indexes(connection: sqlite3.Connection) -> None:
    unique_indexes = set()
    indexes = connection.execute(
        "PRAGMA index_list(agentos_skill_activations)"
    ).fetchall()
    for index in indexes:
        if index[2] != 1:
            continue
        escaped_name = str(index[1]).replace('"', '""')
        columns = connection.execute(
            f'PRAGMA index_info("{escaped_name}")'
        ).fetchall()
        unique_indexes.add(tuple(row[2] for row in columns))
    if unique_indexes != _EXPECTED_UNIQUE_INDEXES:
        raise SkillActivationCorruptedError(
            "skill activation store schema is corrupted"
        )


__all__ = ["SCHEMA_VERSION", "initialize_skill_activation_schema"]
