from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import cast

import aiosqlite

from agentos._sqlite_async import (
    finish_sqlite_operation,
    open_sqlite_connection,
    sqlite_transaction,
)
from agentos.memory.records import (
    MemoryCandidate,
    MemoryCategory,
    MemoryKind,
    MemoryRecord,
    MemorySelectionContext,
)
from agentos.memory.sqlite_errors import (
    SQLiteMemoryStoreClosedError,
    SQLiteMemoryStoreCorruptedError,
)
from agentos.memory.sqlite_schema import initialize_memory_schema


class SQLiteMemoryStore:
    """使用原生异步 SQLite 持久化 Session 范围 Memory。"""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._lock = asyncio.Lock()
        self._connection: aiosqlite.Connection | None = connection

    @classmethod
    async def open(cls, database_path: str | Path) -> SQLiteMemoryStore:
        connection = await open_sqlite_connection(database_path)
        try:
            await connection.execute("PRAGMA busy_timeout = 30000")
            await initialize_memory_schema(connection)
        except BaseException:
            await finish_sqlite_operation(connection.close)
            raise
        return cls(connection)

    async def __aenter__(self) -> SQLiteMemoryStore:
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

    async def put(self, record: MemoryRecord) -> None:
        """在所属 Session 内新增或更新一条 MemoryRecord。"""

        if not isinstance(record, MemoryRecord):
            raise TypeError("record must be a MemoryRecord")
        artifact_handles = json.dumps(
            list(record.artifact_handles),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        expires_at = (
            None if record.expires_at is None else record.expires_at.isoformat()
        )

        async with self._transaction() as connection:
            async with connection.execute(
                "SELECT session_id FROM agentos_memory_records WHERE handle = ?",
                (record.handle,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                existing_session_id = existing[0]
                if not isinstance(existing_session_id, str):
                    raise SQLiteMemoryStoreCorruptedError(
                        "stored memory record is corrupted"
                    )
                if existing_session_id != record.session_id:
                    raise ValueError(
                        "memory handle already belongs to another session",
                    )
            await connection.execute(
                """
                INSERT INTO agentos_memory_records (
                    handle, session_id, kind, category, content,
                    artifact_handles_json, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(handle) DO UPDATE SET
                    session_id = excluded.session_id,
                    kind = excluded.kind,
                    category = excluded.category,
                    content = excluded.content,
                    artifact_handles_json = excluded.artifact_handles_json,
                    expires_at = excluded.expires_at
                """,
                (
                    record.handle,
                    record.session_id,
                    record.kind,
                    record.category,
                    record.content,
                    artifact_handles,
                    expires_at,
                ),
            )

    async def get(self, handle: str) -> MemoryRecord:
        """按稳定 handle 读取 MemoryRecord；不存在时抛出 KeyError。"""

        if not isinstance(handle, str) or not handle.strip():
            raise ValueError("handle must be a non-empty string")
        async with self._lock:
            async with self._require_open().execute(
                f"SELECT {_MEMORY_COLUMNS} "
                "FROM agentos_memory_records WHERE handle = ?",
                (handle,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise KeyError(handle)
        return _record_from_row(row)

    async def search(
        self,
        context: MemorySelectionContext,
        candidate_limit: int,
    ) -> tuple[MemoryCandidate, ...]:
        """在请求 Session 内按确定性词法相关度返回有界候选。"""

        if not isinstance(context, MemorySelectionContext):
            raise TypeError("context must be a MemorySelectionContext")
        if type(candidate_limit) is not int or candidate_limit < 0:
            raise ValueError("candidate_limit must be a non-negative integer")
        if candidate_limit == 0:
            return ()

        async with self._lock:
            async with self._require_open().execute(
                f"SELECT {_MEMORY_COLUMNS} FROM agentos_memory_records "
                "WHERE session_id = ? ORDER BY handle",
                (context.session_id,),
            ) as cursor:
                rows = await cursor.fetchall()

        query_tokens = _tokens(context.query)
        candidates: list[MemoryCandidate] = []
        for row in rows:
            record = _record_from_row(row)
            score, reason = _score(record, query_tokens)
            if query_tokens and score == 0.0:
                continue
            candidates.append(
                MemoryCandidate(record=record, score=score, reason=reason),
            )
        candidates.sort(key=lambda item: (-item.score, item.record.handle))
        return tuple(candidates[:candidate_limit])

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            connection = self._require_open()
            async with sqlite_transaction(connection):
                yield connection

    def _require_open(self) -> aiosqlite.Connection:
        connection = self._connection
        if connection is None:
            raise SQLiteMemoryStoreClosedError("SQLiteMemoryStore is closed")
        return connection


_MEMORY_COLUMNS = (
    "handle, session_id, kind, category, content, "
    "artifact_handles_json, expires_at"
)


def _record_from_row(row: aiosqlite.Row | tuple[object, ...]) -> MemoryRecord:
    try:
        handle, session_id, kind, category, content, handles_json, expiry = row
        text_values = (handle, session_id, kind, category, content, handles_json)
        if any(not isinstance(value, str) for value in text_values):
            raise TypeError("memory text field has an invalid type")
        artifact_handles = json.loads(cast(str, handles_json))
        if not isinstance(artifact_handles, list) or any(
            not isinstance(value, str) for value in artifact_handles
        ):
            raise TypeError("artifact handles must be a list of strings")
        if expiry is not None and not isinstance(expiry, str):
            raise TypeError("memory expiry has an invalid type")
        expires_at = None if expiry is None else datetime.fromisoformat(expiry)
        return MemoryRecord(
            handle=cast(str, handle),
            session_id=cast(str, session_id),
            kind=cast(MemoryKind, kind),
            category=cast(MemoryCategory, category),
            content=cast(str, content),
            artifact_handles=tuple(artifact_handles),
            expires_at=expires_at,
        )
    except (JSONDecodeError, TypeError, ValueError):
        raise SQLiteMemoryStoreCorruptedError(
            "stored memory record is corrupted"
        ) from None


def _score(
    record: MemoryRecord,
    query_tokens: set[str],
) -> tuple[float, str]:
    if not query_tokens:
        return 0.0, "empty query"
    record_tokens = _tokens(
        " ".join((record.handle, record.category, record.content)),
    )
    overlap = query_tokens & record_tokens
    if not overlap:
        return 0.0, "no lexical overlap"
    return (
        len(overlap) / len(query_tokens),
        "lexical overlap: " + ", ".join(sorted(overlap)),
    )


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"\w+", value)}


__all__ = [
    "SQLiteMemoryStore",
    "SQLiteMemoryStoreClosedError",
    "SQLiteMemoryStoreCorruptedError",
]
