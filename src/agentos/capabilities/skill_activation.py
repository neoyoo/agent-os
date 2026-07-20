from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from agentos._sqlite_async import (
    finish_sqlite_operation,
    open_sqlite_connection,
    sqlite_transaction,
)
from agentos.capabilities.skill_activation_errors import (
    SkillActivationCorruptedError,
    SkillActivationStoreClosedError,
)
from agentos.capabilities.skill_activation_schema import (
    initialize_skill_activation_schema,
)
from agentos.capabilities.skill_activation_store import (
    SkillActivationRecord,
    SkillActivationStore,
)
from agentos.capabilities.skill_trust import SkillVerificationSubject


class SQLiteSkillActivationStore:
    """使用原生异步 SQLite 保存 Skill 验证主体摘要。"""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._lock = asyncio.Lock()
        self._connection: aiosqlite.Connection | None = connection

    @classmethod
    async def open(
        cls,
        database_path: str | Path,
    ) -> SQLiteSkillActivationStore:
        connection = await open_sqlite_connection(database_path)
        try:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA busy_timeout = 30000")
            await initialize_skill_activation_schema(connection)
        except BaseException:
            await finish_sqlite_operation(connection.close)
            raise
        return cls(connection)

    async def __aenter__(self) -> SQLiteSkillActivationStore:
        self._require_open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """幂等关闭连接；关闭失败时保留连接以供重试。"""

        async with self._lock:
            connection = self._connection
            if connection is None:
                return
            await finish_sqlite_operation(
                connection.close,
                on_success=lambda: setattr(self, "_connection", None),
            )

    async def save(self, record: SkillActivationRecord) -> None:
        """原子保存或替换一个 Session Skill 激活引用。"""

        if not isinstance(record, SkillActivationRecord):
            raise TypeError("record must be SkillActivationRecord")
        subject = record.subject
        async with self._transaction() as connection:
            await connection.execute(
                """
                INSERT INTO agentos_skill_activations (
                    session_id, skill_name, source_id, source_revision,
                    content_digest, policy_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, skill_name) DO UPDATE SET
                    source_id = excluded.source_id,
                    source_revision = excluded.source_revision,
                    content_digest = excluded.content_digest,
                    policy_id = excluded.policy_id
                """,
                (
                    record.session_id,
                    record.skill_name,
                    subject.source_id,
                    subject.source_revision,
                    subject.content_digest,
                    record.policy_id,
                ),
            )

    async def list(self, session_id: str) -> tuple[SkillActivationRecord, ...]:
        """按 Skill 名称返回一个 Session 的激活引用。"""

        _require_identifier(session_id, "session_id")
        async with self._lock:
            async with self._require_open().execute(
                """
                SELECT session_id, skill_name, source_id, source_revision,
                       content_digest, policy_id
                FROM agentos_skill_activations
                WHERE session_id = ?
                ORDER BY skill_name
                """,
                (session_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(_record_from_row(row) for row in rows)

    async def delete(self, session_id: str, skill_name: str) -> bool:
        """删除一个 Session Skill 激活引用。"""

        _require_identifier(session_id, "session_id")
        _require_identifier(skill_name, "skill_name")
        async with self._transaction() as connection:
            cursor = await connection.execute(
                "DELETE FROM agentos_skill_activations "
                "WHERE session_id = ? AND skill_name = ?",
                (session_id, skill_name),
            )
            try:
                return cursor.rowcount == 1
            finally:
                await cursor.close()

    async def delete_session(self, session_id: str) -> None:
        """删除一个 Session 的全部 Skill 激活引用。"""

        _require_identifier(session_id, "session_id")
        async with self._transaction() as connection:
            await connection.execute(
                "DELETE FROM agentos_skill_activations WHERE session_id = ?",
                (session_id,),
            )

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            connection = self._require_open()
            async with sqlite_transaction(connection):
                yield connection

    def _require_open(self) -> aiosqlite.Connection:
        connection = self._connection
        if connection is None:
            raise SkillActivationStoreClosedError(
                "SQLiteSkillActivationStore is closed",
            )
        return connection


def _record_from_row(row: aiosqlite.Row) -> SkillActivationRecord:
    try:
        values = tuple(row[key] for key in row.keys())
        if any(not isinstance(value, str) for value in values):
            raise TypeError("activation field has an invalid type")
        session_id, skill_name, source_id, source_revision, digest, policy_id = values
        return SkillActivationRecord(
            session_id=session_id,
            subject=SkillVerificationSubject(
                source_id=source_id,
                skill_name=skill_name,
                source_revision=source_revision,
                content_digest=digest,
            ),
            policy_id=policy_id,
        )
    except (IndexError, KeyError, TypeError, ValueError):
        raise SkillActivationCorruptedError(
            "stored skill activation is corrupted",
        ) from None


def _require_identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


__all__ = [
    "SQLiteSkillActivationStore",
    "SkillActivationCorruptedError",
    "SkillActivationRecord",
    "SkillActivationStore",
    "SkillActivationStoreClosedError",
]
