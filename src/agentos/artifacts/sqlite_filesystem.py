from __future__ import annotations

import base64
import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

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
    """使用 SQLite metadata 和文件系统 bytes 的 Level 2 ArtifactStore。"""

    def __init__(
        self,
        *,
        database_path: str | Path,
        artifact_root: str | Path,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._database_path = Path(database_path).resolve()
        self._blobs = FilesystemArtifactBlobs(artifact_root)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or new_artifact_id
        self._lock = RLock()
        self._closed = False
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        self._recover_staged_deletes()

    def close(self) -> None:
        """幂等关闭 Store，并拒绝后续 metadata 与内容访问。"""

        with self._lock:
            self._closed = True

    def put(
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
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = ArtifactRecord(
                id=self._id_factory(),
                session_id=session_id,
                filename=filename,
                media_type=media_type,
                size_bytes=len(data),
                created_at=self._clock(),
            )
            blob_key = self._blobs.key(record.id)
            self._reject_collision(connection, record.id)
            promoted = False
            try:
                self._blobs.write_exclusive(record.id, data)
                promoted = True
                connection.execute(
                    """
                    INSERT INTO durable_artifacts (
                        artifact_id, session_id, filename, media_type,
                        size_bytes, created_at, blob_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (record.id, record.session_id, record.filename,
                     record.media_type, record.size_bytes,
                     record.created_at.isoformat(timespec="microseconds"),
                     blob_key),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                if promoted:
                    self._blobs.delete(record.id)
                raise
            return record

    def get(self, session_id: str, artifact_id: str) -> ArtifactRecord:
        """返回 Session 内 metadata，未知和跨 Session 使用相同错误。"""
        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        with self._lock, self._connect() as connection:
            row = self._select_scoped(connection, session_id, artifact_id)
            return _record_from_row(row)

    def read(self, session_id: str, artifact_id: str) -> bytes:
        """从受控路径读取 bytes，不暴露磁盘路径。"""
        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        with self._lock, self._connect() as connection:
            row = self._select_scoped(connection, session_id, artifact_id)
            return self._blobs.read(artifact_id, row[6])

    def list(
        self,
        session_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ArtifactPage:
        """按 created_at、artifact_id 倒序返回稳定分页。"""
        validate_artifact_session_id(session_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ArtifactValidationError("artifact limit is invalid")
        with self._lock, self._connect() as connection:
            parameters: list[object] = [session_id]
            cursor_clause = ""
            if cursor is not None:
                anchor_id = _decode_cursor(cursor)
                try:
                    anchor = self._select_scoped(connection, session_id, anchor_id)
                except ArtifactNotFoundError:
                    raise ArtifactValidationError("invalid artifact cursor") from None
                cursor_clause = (
                    "AND (created_at < ? OR "
                    "(created_at = ? AND artifact_id < ?))"
                )
                parameters.extend((anchor[5], anchor[5], anchor[0]))
            parameters.append(limit + 1)
            rows = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM durable_artifacts "
                f"WHERE session_id = ? {cursor_clause} "
                "ORDER BY created_at DESC, artifact_id DESC LIMIT ?",
                parameters,
            ).fetchall()
            records = tuple(_record_from_row(row) for row in rows[:limit])
            next_cursor = None
            if len(rows) > limit:
                next_cursor = _encode_cursor(records[-1].id)
            return ArtifactPage(items=records, next_cursor=next_cursor)

    def delete(self, session_id: str, artifact_id: str) -> None:
        """删除 Session 内 metadata 和由 Artifact ID 派生的受控 blob。"""
        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._select_scoped(connection, session_id, artifact_id)
            deletion = self._blobs.stage_delete(artifact_id)
            staged = () if deletion is None else (deletion,)
            try:
                connection.execute(
                    "DELETE FROM durable_artifacts "
                    "WHERE session_id = ? AND artifact_id = ?",
                    (session_id, artifact_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                self._restore_deletes(staged)
                raise
            self._commit_deletes(staged)

    def delete_session(self, session_id: str) -> None:
        """幂等删除 Session metadata 和对应受控 blob。"""
        validate_artifact_session_id(session_id)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT artifact_id, blob_key FROM durable_artifacts "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            for artifact_id, blob_key in rows:
                validate_artifact_id(str(artifact_id))
                self._blobs.validate_key(str(artifact_id), blob_key)
            staged: list[ArtifactBlobDelete] = []
            try:
                for artifact_id, _blob_key in rows:
                    deletion = self._blobs.stage_delete(str(artifact_id))
                    if deletion is not None:
                        staged.append(deletion)
                connection.execute(
                    "DELETE FROM durable_artifacts WHERE session_id = ?",
                    (session_id,),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                self._restore_deletes(tuple(staged))
                raise
            self._commit_deletes(tuple(staged))

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS durable_artifacts ("
                "artifact_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
                "filename TEXT, media_type TEXT NOT NULL, "
                "size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0), "
                "created_at TEXT NOT NULL, blob_key TEXT NOT NULL UNIQUE);"
                "CREATE INDEX IF NOT EXISTS idx_durable_artifacts_session_order "
                "ON durable_artifacts(session_id, created_at DESC, artifact_id DESC);"
            )

    def _recover_staged_deletes(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for deletion in self._blobs.pending_deletes():
                    row = connection.execute(
                        "SELECT 1 FROM durable_artifacts WHERE artifact_id = ?",
                        (deletion.artifact_id,),
                    ).fetchone()
                    if row is None:
                        self._blobs.commit_delete(deletion)
                    else:
                        self._blobs.restore_delete(deletion)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _restore_deletes(
        self,
        staged: tuple[ArtifactBlobDelete, ...],
    ) -> None:
        error: BaseException | None = None
        for deletion in reversed(staged):
            try:
                self._blobs.restore_delete(deletion)
            except BaseException as caught:
                if error is None:
                    error = caught
        if error is not None:
            raise error

    def _commit_deletes(
        self,
        staged: tuple[ArtifactBlobDelete, ...],
    ) -> None:
        for deletion in staged:
            try:
                self._blobs.commit_delete(deletion)
            except ArtifactError:
                logger.warning("artifact staging cleanup deferred")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        connection = sqlite3.connect(self._database_path, timeout=30)
        try:
            yield connection
        finally:
            connection.close()

    def _ensure_open(self) -> None:
        if self._closed:
            from agentos.runtime.errors import DurableStoreClosedError

            raise DurableStoreClosedError("durable artifact store is closed")

    def _select_scoped(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        artifact_id: str,
    ) -> tuple[object, ...]:
        row = connection.execute(
            f"SELECT {_SELECT_COLUMNS} FROM durable_artifacts "
            "WHERE session_id = ? AND artifact_id = ?",
            (session_id, artifact_id),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError()
        return row

    def _reject_collision(
        self,
        connection: sqlite3.Connection,
        artifact_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM durable_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if (
            row is not None
            or self._blobs.exists(artifact_id)
            or any(
                deletion.artifact_id == artifact_id
                for deletion in self._blobs.pending_deletes()
            )
        ):
            raise ArtifactValidationError("artifact id collision")


def _record_from_row(row: tuple[object, ...]) -> ArtifactRecord:
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
