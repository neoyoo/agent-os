from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock

from agentos.artifacts.types import (
    ArtifactNotFoundError,
    ArtifactPage,
    ArtifactRecord,
    ArtifactValidationError,
    new_artifact_id,
    validate_artifact_id,
    validate_artifact_session_id,
)


class InMemoryArtifactStore:
    """线程安全的 Level 1 ArtifactStore Adapter。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or new_artifact_id
        self._records: dict[str, ArtifactRecord] = {}
        self._contents: dict[str, bytes] = {}
        self._lock = RLock()

    def put(
        self,
        *,
        session_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        """原子保存 metadata 与 bytes；相同内容仍获得独立 ID。"""

        if type(data) is not bytes:
            raise ArtifactValidationError("artifact data must be bytes")
        validate_artifact_session_id(session_id)
        with self._lock:
            record = ArtifactRecord(
                id=self._id_factory(),
                session_id=session_id,
                filename=filename,
                media_type=media_type,
                size_bytes=len(data),
                created_at=self._clock(),
            )
            if record.id in self._records:
                raise ArtifactValidationError("artifact id collision")
            self._records[record.id] = record
            self._contents[record.id] = data
            return record

    def get(self, session_id: str, artifact_id: str) -> ArtifactRecord:
        """返回 Session 内元数据，未知与跨 Session 使用同一错误。"""

        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        with self._lock:
            return self._record_for_session(session_id, artifact_id)

    def read(self, session_id: str, artifact_id: str) -> bytes:
        """返回 Session 内原始内容，且不暴露存储实现。"""

        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        with self._lock:
            record = self._record_for_session(session_id, artifact_id)
            return self._contents[record.id]

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
        with self._lock:
            records = sorted(
                (
                    record
                    for record in self._records.values()
                    if record.session_id == session_id
                ),
                key=_sort_key,
                reverse=True,
            )
            if cursor is not None:
                anchor_id = _decode_cursor(cursor)
                try:
                    anchor = self._record_for_session(session_id, anchor_id)
                except (ArtifactNotFoundError, ArtifactValidationError):
                    raise _invalid_cursor() from None
                anchor_key = _sort_key(anchor)
                records = [record for record in records if _sort_key(record) < anchor_key]
            page_items = tuple(records[:limit])
            next_cursor = None
            if len(records) > limit:
                next_cursor = _encode_cursor(page_items[-1].id)
            return ArtifactPage(items=page_items, next_cursor=next_cursor)

    def delete(self, session_id: str, artifact_id: str) -> None:
        """原子删除 Session 内 metadata 与 bytes。"""

        validate_artifact_session_id(session_id)
        validate_artifact_id(artifact_id)
        with self._lock:
            record = self._record_for_session(session_id, artifact_id)
            del self._contents[record.id]
            del self._records[record.id]

    def delete_session(self, session_id: str) -> None:
        """幂等删除一个 Session 的全部 Artifact。"""

        validate_artifact_session_id(session_id)
        with self._lock:
            artifact_ids = tuple(
                record.id
                for record in self._records.values()
                if record.session_id == session_id
            )
            for artifact_id in artifact_ids:
                del self._contents[artifact_id]
                del self._records[artifact_id]

    def _record_for_session(
        self,
        session_id: str,
        artifact_id: str,
    ) -> ArtifactRecord:
        record = self._records.get(artifact_id)
        if record is None or record.session_id != session_id:
            raise ArtifactNotFoundError()
        return record


def _sort_key(record: ArtifactRecord) -> tuple[datetime, str]:
    return record.created_at, record.id


def _encode_cursor(artifact_id: str) -> str:
    raw = json.dumps(
        {"artifact_id": artifact_id, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> str:
    if type(cursor) is not str or not cursor:
        raise _invalid_cursor()
    try:
        padding = b"=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor.encode("ascii") + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise _invalid_cursor() from None
    if (
        type(payload) is not dict
        or set(payload) != {"artifact_id", "version"}
        or payload.get("version") != 1
        or type(payload.get("artifact_id")) is not str
    ):
        raise _invalid_cursor()
    artifact_id = payload["artifact_id"]
    try:
        validate_artifact_id(artifact_id)
    except ArtifactValidationError:
        raise _invalid_cursor() from None
    return artifact_id


def _invalid_cursor() -> ArtifactValidationError:
    return ArtifactValidationError("invalid artifact cursor")
