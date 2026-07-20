from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from agentos._sqlite_async import (
    finish_sqlite_operation,
    open_sqlite_connection,
    sqlite_transaction,
)
from agentos.artifacts._filesystem_async import (
    run_filesystem_operation as _offload,
)
from agentos.artifacts.sqlite_blobs import (
    ArtifactBlobDelete,
    ArtifactContentMissingError as ArtifactContentMissingError,
    FilesystemArtifactBlobs,
)
from agentos.artifacts.types import (
    ArtifactError,
    ArtifactNotFoundError,
    ArtifactPage,
    ArtifactRecord,
    ArtifactValidationError,
    new_artifact_id,
    validate_artifact_id,
    validate_artifact_session_id,
)
from agentos.runtime._async_bridge import _await_cleanup_preserving_cancellation
from agentos.runtime.errors import DurableStoreClosedError


_SELECT_COLUMNS = (
    "artifact_id, session_id, filename, media_type, "
    "size_bytes, created_at, blob_key"
)

logger = logging.getLogger(__name__)


class ArtifactMetadataCorruptedError(ArtifactError):
    """Artifact metadata 无法通过严格校验。"""

    def __init__(self) -> None:
        super().__init__("artifact metadata corrupted")


class SqliteFilesystemArtifactStore:
    """使用异步 SQLite metadata 和受控文件系统 bytes 的 ArtifactStore。"""

    def __init__(
        self,
        *,
        connection: aiosqlite.Connection,
        blobs: FilesystemArtifactBlobs,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._connection: aiosqlite.Connection | None = connection
        self._blobs = blobs
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or new_artifact_id
        self._lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        *,
        database_path: str | Path,
        artifact_root: str | Path,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> SqliteFilesystemArtifactStore:
        database = Path(database_path)
        await _offload(database.parent.mkdir, parents=True, exist_ok=True)
        blobs = await _offload(FilesystemArtifactBlobs, artifact_root)
        connection = await open_sqlite_connection(database, isolation_level=None)
        try:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA busy_timeout = 30000")
            store = cls(
                connection=connection,
                blobs=blobs,
                clock=clock,
                id_factory=id_factory,
            )
            await store._initialize_schema()
            await store._recover_staged_deletes()
        except BaseException:
            await finish_sqlite_operation(connection.close)
            raise
        return store

    async def __aenter__(self) -> SqliteFilesystemArtifactStore:
        self._ensure_open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """幂等关闭 Store，并拒绝后续 metadata 与内容访问。"""

        async with self._lock:
            connection = self._connection
            if connection is None:
                return
            await finish_sqlite_operation(
                connection.close,
                on_success=lambda: setattr(self, "_connection", None),
            )

    async def put(
        self,
        *,
        session_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        """先原子写入 bytes，再提交 metadata，失败时清理文件残留。"""

        if type(data) is not bytes:
            raise ArtifactValidationError("artifact data must be bytes")
        validate_artifact_session_id(session_id)
        record = ArtifactRecord(
            id=self._id_factory(),
            session_id=session_id,
            filename=filename,
            media_type=media_type,
            size_bytes=len(data),
            created_at=self._clock(),
        )
        promoted = False
        committed = False

        def mark_promoted() -> None:
            nonlocal promoted
            promoted = True

        def mark_committed() -> None:
            nonlocal committed
            committed = True

        try:
            async with self._transaction(on_commit=mark_committed) as connection:
                await self._reject_collision(connection, record.id)
                await _offload(
                    self._blobs.write_exclusive,
                    record.id,
                    data,
                    on_success=mark_promoted,
                )
                await connection.execute(
                    """
                    INSERT INTO durable_artifacts (
                        artifact_id, session_id, filename, media_type,
                        size_bytes, created_at, blob_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.session_id,
                        record.filename,
                        record.media_type,
                        record.size_bytes,
                        record.created_at.isoformat(timespec="microseconds"),
                        self._blobs.key(record.id),
                    ),
                )
        except BaseException:
            if promoted and not committed:
                await _offload(self._blobs.delete, record.id)
            raise
        return record

    async def get(self, session_id: str, artifact_id: str) -> ArtifactRecord:
        """返回 Session 内 metadata，未知和跨 Session 使用相同错误。"""

        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        async with self._lock:
            row = await self._select_scoped(
                self._ensure_open(),
                session_id,
                artifact_id,
            )
        return _record_from_row(row)

    async def read(self, session_id: str, artifact_id: str) -> bytes:
        """从受控路径读取 bytes，不暴露磁盘路径。"""

        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        async with self._lock:
            row = await self._select_scoped(
                self._ensure_open(),
                session_id,
                artifact_id,
            )
            return await _offload(self._blobs.read, artifact_id, row[6])

    async def list(
        self,
        session_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ArtifactPage:
        """按 created_at、artifact_id 倒序返回稳定分页。"""

        validate_artifact_session_id(session_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ArtifactValidationError("artifact limit is invalid")
        async with self._lock:
            connection = self._ensure_open()
            parameters: list[object] = [session_id]
            cursor_clause = ""
            if cursor is not None:
                anchor_id = _decode_cursor(cursor)
                try:
                    anchor = await self._select_scoped(
                        connection,
                        session_id,
                        anchor_id,
                    )
                except ArtifactNotFoundError:
                    raise ArtifactValidationError("invalid artifact cursor") from None
                cursor_clause = (
                    "AND (created_at < ? OR "
                    "(created_at = ? AND artifact_id < ?))"
                )
                parameters.extend((anchor[5], anchor[5], anchor[0]))
            parameters.append(limit + 1)
            async with connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM durable_artifacts "
                f"WHERE session_id = ? {cursor_clause} "
                "ORDER BY created_at DESC, artifact_id DESC LIMIT ?",
                parameters,
            ) as query:
                rows = await query.fetchall()
        records = tuple(_record_from_row(row) for row in rows[:limit])
        next_cursor = _encode_cursor(records[-1].id) if len(rows) > limit else None
        return ArtifactPage(items=records, next_cursor=next_cursor)

    async def delete(self, session_id: str, artifact_id: str) -> None:
        """删除 Session 内 metadata 和由 Artifact ID 派生的受控 blob。"""

        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        deletion: ArtifactBlobDelete | None = None
        committed = False

        def mark_committed() -> None:
            nonlocal committed
            committed = True

        try:
            async with self._transaction(on_commit=mark_committed) as connection:
                await self._select_scoped(connection, session_id, artifact_id)
                deletion = await self._stage_delete(artifact_id)
                await connection.execute(
                    "DELETE FROM durable_artifacts "
                    "WHERE session_id = ? AND artifact_id = ?",
                    (session_id, artifact_id),
                )
        except BaseException:
            if deletion is not None and not committed:
                await _offload(self._blobs.restore_delete, deletion)
            raise
        if deletion is not None:
            await self._commit_delete(deletion)

    async def delete_session(self, session_id: str) -> None:
        """幂等删除 Session metadata 和对应受控 blob。"""

        validate_artifact_session_id(session_id)
        staged: list[ArtifactBlobDelete] = []
        committed = False

        def mark_committed() -> None:
            nonlocal committed
            committed = True

        try:
            async with self._transaction(on_commit=mark_committed) as connection:
                async with connection.execute(
                    "SELECT artifact_id, blob_key FROM durable_artifacts "
                    "WHERE session_id = ?",
                    (session_id,),
                ) as query:
                    rows = await query.fetchall()
                for artifact_id, blob_key in rows:
                    validate_artifact_id(str(artifact_id))
                    self._blobs.validate_key(str(artifact_id), blob_key)
                for artifact_id, _blob_key in rows:
                    deletion = await self._stage_delete(str(artifact_id))
                    if deletion is not None:
                        staged.append(deletion)
                await connection.execute(
                    "DELETE FROM durable_artifacts WHERE session_id = ?",
                    (session_id,),
                )
        except BaseException:
            if not committed:
                await self._restore_deletes(tuple(staged))
            raise
        for deletion in staged:
            await self._commit_delete(deletion)

    async def _initialize_schema(self) -> None:
        async with self._lock:
            await self._ensure_open().executescript(
                "CREATE TABLE IF NOT EXISTS durable_artifacts ("
                "artifact_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
                "filename TEXT, media_type TEXT NOT NULL, "
                "size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0), "
                "created_at TEXT NOT NULL, blob_key TEXT NOT NULL UNIQUE);"
                "CREATE INDEX IF NOT EXISTS idx_durable_artifacts_session_order "
                "ON durable_artifacts(session_id, created_at DESC, artifact_id DESC);"
            )

    async def _recover_staged_deletes(self) -> None:
        async with self._transaction() as connection:
            pending = await _offload(self._blobs.pending_deletes)
            for deletion in pending:
                async with connection.execute(
                    "SELECT 1 FROM durable_artifacts WHERE artifact_id = ?",
                    (deletion.artifact_id,),
                ) as query:
                    row = await query.fetchone()
                operation = (
                    self._blobs.commit_delete
                    if row is None
                    else self._blobs.restore_delete
                )
                await _offload(operation, deletion)

    async def _restore_deletes(
        self,
        staged: tuple[ArtifactBlobDelete, ...],
    ) -> None:
        error: BaseException | None = None
        for deletion in reversed(staged):
            try:
                await _offload(self._blobs.restore_delete, deletion)
            except BaseException as caught:
                if error is None:
                    error = caught
        if error is not None:
            raise error

    async def _commit_delete(self, deletion: ArtifactBlobDelete) -> None:
        try:
            await _offload(self._blobs.commit_delete, deletion)
        except ArtifactError:
            logger.warning("artifact staging cleanup deferred")

    async def _stage_delete(self, artifact_id: str) -> ArtifactBlobDelete | None:
        task = asyncio.create_task(
            asyncio.to_thread(self._blobs.stage_delete, artifact_id),
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            async def finish_and_restore() -> None:
                deletion = await task
                if deletion is not None:
                    await asyncio.to_thread(self._blobs.restore_delete, deletion)

            await _await_cleanup_preserving_cancellation(finish_and_restore)
            raise

    async def _select_scoped(
        self,
        connection: aiosqlite.Connection,
        session_id: str,
        artifact_id: str,
    ) -> aiosqlite.Row:
        async with connection.execute(
            f"SELECT {_SELECT_COLUMNS} FROM durable_artifacts "
            "WHERE session_id = ? AND artifact_id = ?",
            (session_id, artifact_id),
        ) as query:
            row = await query.fetchone()
        if row is None:
            raise ArtifactNotFoundError()
        return row

    async def _reject_collision(
        self,
        connection: aiosqlite.Connection,
        artifact_id: str,
    ) -> None:
        async with connection.execute(
            "SELECT 1 FROM durable_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ) as query:
            row = await query.fetchone()
        blob_exists = await _offload(self._blobs.exists, artifact_id)
        pending = await _offload(self._blobs.pending_deletes)
        if (
            row is not None
            or blob_exists
            or any(deletion.artifact_id == artifact_id for deletion in pending)
        ):
            raise ArtifactValidationError("artifact id collision")

    @asynccontextmanager
    async def _transaction(
        self,
        *,
        on_commit: Callable[[], None] | None = None,
    ) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            connection = self._ensure_open()
            async with sqlite_transaction(connection, on_commit=on_commit):
                yield connection

    def _ensure_open(self) -> aiosqlite.Connection:
        connection = self._connection
        if connection is None:
            raise DurableStoreClosedError("durable artifact store is closed")
        return connection
def _record_from_row(row: aiosqlite.Row) -> ArtifactRecord:
    try:
        artifact_id, session_id, filename, media_type, size, created_at, _key = row
        if (
            not isinstance(artifact_id, str)
            or not isinstance(session_id, str)
            or (filename is not None and not isinstance(filename, str))
            or not isinstance(media_type, str)
            or type(size) is not int
            or not isinstance(created_at, str)
        ):
            raise TypeError("artifact metadata field is invalid")
        return ArtifactRecord(
            artifact_id,
            session_id,
            filename,
            media_type,
            size,
            datetime.fromisoformat(created_at),
        )
    except (TypeError, ValueError):
        raise ArtifactMetadataCorruptedError() from None


def _encode_cursor(artifact_id: str) -> str:
    payload = {"artifact_id": artifact_id, "version": 1}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> str:
    try:
        if type(cursor) is not str or not cursor:
            raise ValueError
        padding = b"=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor.encode("ascii") + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
        if (
            type(payload) is not dict
            or set(payload) != {"artifact_id", "version"}
            or payload.get("version") != 1
            or type(payload.get("artifact_id")) is not str
        ):
            raise ValueError
        artifact_id = payload["artifact_id"]
        validate_artifact_id(artifact_id)
        if cursor != _encode_cursor(artifact_id):
            raise ValueError
    except (UnicodeError, ValueError):
        raise ArtifactValidationError("invalid artifact cursor") from None
    return artifact_id
