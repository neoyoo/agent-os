import json
import sqlite3
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path

from agentos.observability.events import event_record_to_dict
from agentos.persistence.base import (
    SessionSnapshot,
    SnapshotLoadError,
    SnapshotVersionError,
)
from agentos.persistence.serializers import (
    session_snapshot_from_dict,
    session_snapshot_to_dict,
)


class SQLitePersistence:
    """以 SQLite 保存 session snapshot 和 append-only event records。"""

    def __init__(self, path: Path) -> None:
        """创建 SQLite persistence；schema 必须由迁移流程预先准备。"""

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: SessionSnapshot) -> None:
        """保存最新 snapshot，并替换该 session 的 event records。"""

        now = datetime.now(timezone.utc).isoformat()
        payload = session_snapshot_to_dict(snapshot)
        session_id = snapshot.session_state.id
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO snapshots (session_id, version, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  version = excluded.version,
                  payload_json = excluded.payload_json,
                  updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    snapshot.version,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM event_records WHERE session_id = ?",
                (session_id,),
            )
            for record in snapshot.event_records:
                record_dict = event_record_to_dict(record)
                connection.execute(
                    """
                    INSERT INTO event_records (
                      session_id, sequence, event_type, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        record_dict["sequence"],
                        record_dict["event_type"],
                        json.dumps(record_dict, ensure_ascii=False),
                        record_dict["created_at"],
                    ),
                )

    def load(self, session_id: str) -> SessionSnapshot:
        """读取一个 session snapshot。"""

        with self._connect() as connection:
            snapshot_row = connection.execute(
                "SELECT payload_json FROM snapshots WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if snapshot_row is None:
                raise KeyError(session_id)
            try:
                payload = json.loads(snapshot_row[0])
                event_rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM event_records
                    WHERE session_id = ?
                    ORDER BY sequence
                    """,
                    (session_id,),
                ).fetchall()
                payload["event_records"] = [
                    json.loads(row[0])
                    for row in event_rows
                ]
                return session_snapshot_from_dict(payload)
            except SnapshotVersionError:
                raise
            except (JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise SnapshotLoadError(
                    f"failed to load snapshot {session_id!r}: {error}",
                ) from error

    def list_ids(self) -> list[str]:
        """列出已保存的 session ids。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id FROM snapshots ORDER BY session_id",
            ).fetchall()
        return [str(row[0]) for row in rows]

    def delete(self, session_id: str) -> None:
        """删除一个 session snapshot 和对应事件。"""

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM event_records WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM snapshots WHERE session_id = ?",
                (session_id,),
            )

    def _connect(self) -> sqlite3.Connection:
        """创建 SQLite 连接并启用 WAL。"""

        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection
