from __future__ import annotations

import json
from typing import Protocol, Sequence, cast

from agentos.context import CompressedSegment
from agentos.memory.serializers import (
    message_from_dict,
    message_ref_from_dict,
    message_ref_to_dict,
    message_to_dict,
    package_from_dict,
    package_to_dict,
)
from agentos.memory.types import CompressedSegmentPackage
from agentos.messages import Message, MessageRef
from agentos.runtime.session import SessionState


class PostgresCursor(Protocol):
    """Postgres cursor 的最小类型边界。"""

    def fetchone(self) -> tuple[object, ...] | None:
        """读取一行。"""

    def fetchall(self) -> list[tuple[object, ...]]:
        """读取全部行。"""


class PostgresConnection(Protocol):
    """Postgres connection 的最小执行边界。"""

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresCursor:
        """执行 SQL 并返回 cursor。"""


class PostgresDurableSessionStore:
    """Postgres-backed DurableSessionStore adapter。"""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
    ) -> None:
        """创建 Postgres durable store；未安装 postgres extra 时给出清晰错误。"""

        if connection is not None:
            self._connection = connection
            self._dsn = dsn
            return
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgresDurableSessionStore requires the optional dependency "
                "`agentos[postgres]`.",
            ) from error
        self._connection = psycopg.connect(dsn)
        self._dsn = dsn

    def save_session(self, session: SessionState) -> None:
        """保存 session state。"""

        self._execute(
            """
            INSERT INTO agentos_sessions (session_id, status, next_turn_number)
            VALUES (%s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                status = EXCLUDED.status,
                next_turn_number = EXCLUDED.next_turn_number,
                updated_at = now()
            """,
            (session.id, session.status, session.next_turn_number()),
        )
        self._commit()

    def load_session(self, session_id: str) -> SessionState:
        """读取 session state。"""

        row = self._execute(
            """
            SELECT status, next_turn_number FROM agentos_sessions
            WHERE session_id = %s
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return SessionState.from_snapshot(
            id=session_id,
            status=row[0],
            next_turn_number=int(row[1]),
        )

    def append_message(self, session_id: str, message: Message) -> None:
        """追加原始消息。"""

        self._execute(
            """
            INSERT INTO agentos_messages (session_id, message_id, payload)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (session_id, message_id) DO UPDATE SET
                payload = EXCLUDED.payload,
                updated_at = now()
            """,
            (
                session_id,
                message.id,
                json.dumps(message_to_dict(message), ensure_ascii=False),
            ),
        )
        self._commit()

    def get_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[Message]:
        """按 ids 读取原始消息，返回顺序与 message_ids 一致。"""

        if not message_ids:
            return []

        rows = self._execute(
            """
            SELECT message_id, payload FROM agentos_messages
            WHERE session_id = %s AND message_id = ANY(%s)
            """,
            (session_id, list(message_ids)),
        ).fetchall()
        payloads_by_id = {
            str(row[0]): row[1]
            for row in rows
        }
        messages: list[Message] = []
        for message_id in message_ids:
            if message_id not in payloads_by_id:
                raise KeyError(message_id)
            messages.append(
                message_from_dict(self._json_value(payloads_by_id[message_id])),
            )
        return messages

    def save_active_refs(
        self,
        session_id: str,
        refs: Sequence[MessageRef],
    ) -> None:
        """保存 active refs checkpoint。"""

        self._execute(
            """
            INSERT INTO agentos_active_refs (session_id, refs)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (session_id) DO UPDATE SET
                refs = EXCLUDED.refs,
                updated_at = now()
            """,
            (
                session_id,
                json.dumps(
                    [message_ref_to_dict(ref) for ref in refs],
                    ensure_ascii=False,
                ),
            ),
        )
        self._commit()

    def load_active_refs(self, session_id: str) -> tuple[MessageRef, ...]:
        """读取 active refs checkpoint。"""

        row = self._execute(
            """
            SELECT refs FROM agentos_active_refs
            WHERE session_id = %s
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return ()
        return tuple(message_ref_from_dict(ref) for ref in self._json_value(row[0]))

    def save_compressed_segment(
        self,
        session_id: str,
        package: CompressedSegmentPackage,
    ) -> None:
        """保存 compressed segment package。"""

        self._execute(
            """
            INSERT INTO agentos_compressed_segments
                (session_id, segment_id, package, source_refs)
            VALUES (%s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (session_id, segment_id) DO UPDATE SET
                package = EXCLUDED.package,
                source_refs = EXCLUDED.source_refs,
                updated_at = now()
            """,
            (
                session_id,
                package.segment.id,
                json.dumps(package_to_dict(package), ensure_ascii=False),
                json.dumps(list(package.source_refs), ensure_ascii=False),
            ),
        )
        self._commit()

    def get_segment_refs(self, session_id: str, segment_id: str) -> tuple[str, ...]:
        """读取 durable segment refs。"""

        row = self._execute(
            """
            SELECT source_refs FROM agentos_compressed_segments
            WHERE session_id = %s AND segment_id = %s
            """,
            (session_id, segment_id),
        ).fetchone()
        if row is None:
            raise KeyError(segment_id)
        return tuple(str(ref) for ref in self._json_value(row[0]))

    def list_compressed_segments(
        self,
        session_id: str,
    ) -> tuple[CompressedSegment, ...]:
        """列出 session 下的 LLM 可见 compressed segments。"""

        rows = self._execute(
            """
            SELECT package FROM agentos_compressed_segments
            WHERE session_id = %s
            ORDER BY segment_id
            """,
            (session_id,),
        ).fetchall()
        return tuple(package_from_dict(self._json_value(row[0])).segment for row in rows)

    def _execute(
        self,
        sql: str,
        params: tuple[object, ...] | None = None,
    ) -> PostgresCursor:
        connection = cast(PostgresConnection, self._connection)
        return connection.execute(sql, params or ())

    def _commit(self) -> None:
        commit = getattr(self._connection, "commit", None)
        if commit is not None:
            commit()

    def _json_value(self, value: object) -> object:
        """兼容 psycopg JSONB dict/list 返回值和测试 fake 的 JSON str。"""

        if isinstance(value, str):
            return json.loads(value)
        return value
