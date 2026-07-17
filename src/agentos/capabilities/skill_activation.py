from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from types import TracebackType

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
    """使用 SQLite 保存 Skill 验证主体摘要，不保存正文。"""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            path,
            timeout=5.0,
            check_same_thread=False,
        )
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 5000")
            initialize_skill_activation_schema(self._connection)
        except BaseException:
            self._connection.close()
            self._connection = None
            raise

    def __enter__(self) -> SQLiteSkillActivationStore:
        with self._lock:
            self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """幂等关闭 SQLite 连接。"""

        with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                connection.close()

    def save(self, record: SkillActivationRecord) -> None:
        """原子保存或替换一个 Session Skill 激活引用。"""

        if not isinstance(record, SkillActivationRecord):
            raise TypeError("record must be SkillActivationRecord")
        subject = record.subject
        with self._lock:
            connection = self._require_open()
            with connection:
                connection.execute(
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

    def list(self, session_id: str) -> tuple[SkillActivationRecord, ...]:
        """按 Skill 名称返回一个 Session 的激活引用。"""

        _require_identifier(session_id, "session_id")
        with self._lock:
            rows = self._require_open().execute(
                """
                SELECT session_id, skill_name, source_id, source_revision,
                       content_digest, policy_id
                FROM agentos_skill_activations
                WHERE session_id = ?
                ORDER BY skill_name
                """,
                (session_id,),
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def delete(self, session_id: str, skill_name: str) -> bool:
        """删除一个 Session Skill 激活引用。"""

        _require_identifier(session_id, "session_id")
        _require_identifier(skill_name, "skill_name")
        with self._lock:
            connection = self._require_open()
            with connection:
                cursor = connection.execute(
                    "DELETE FROM agentos_skill_activations "
                    "WHERE session_id = ? AND skill_name = ?",
                    (session_id, skill_name),
                )
        return cursor.rowcount == 1

    def delete_session(self, session_id: str) -> None:
        """删除一个 Session 的全部 Skill 激活引用。"""

        _require_identifier(session_id, "session_id")
        with self._lock:
            connection = self._require_open()
            with connection:
                connection.execute(
                    "DELETE FROM agentos_skill_activations WHERE session_id = ?",
                    (session_id,),
                )

    def _require_open(self) -> sqlite3.Connection:
        if self._connection is None:
            raise SkillActivationStoreClosedError(
                "SQLiteSkillActivationStore is closed",
            )
        return self._connection


def _record_from_row(row: sqlite3.Row) -> SkillActivationRecord:
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
