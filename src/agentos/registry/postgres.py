from __future__ import annotations

import json
from typing import cast

from agentos.persistence.protocols import PostgresConnection, PostgresCursor
from agentos.registry.serializers import (
    affinity_from_dict,
    card_from_dict,
    card_to_dict,
)
from agentos.registry.types import AgentRegistryRecord, SessionAffinity


class PostgresAgentRegistryStore:
    """Postgres-backed AgentRegistryStore adapter。"""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
    ) -> None:
        """创建 Postgres registry store；未安装 postgres extra 时给出清晰错误。"""

        if connection is not None:
            self._connection = connection
            self._dsn = dsn
            return
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgresAgentRegistryStore requires the optional dependency "
                "`agentos[postgres]`.",
            ) from error
        self._connection = psycopg.connect(dsn)
        self._dsn = dsn

    def save_record(self, record: AgentRegistryRecord) -> None:
        """保存 agent 注册记录。"""

        self._execute(
            """
            INSERT INTO agentos_agent_registry (agent_id, card, heartbeat_at)
            VALUES (%s, %s::jsonb, %s)
            ON CONFLICT (agent_id) DO UPDATE SET
                card = EXCLUDED.card,
                heartbeat_at = EXCLUDED.heartbeat_at,
                updated_at = now()
            """,
            (
                record.card.agent_id,
                json.dumps(card_to_dict(record.card), ensure_ascii=False),
                record.heartbeat_at,
            ),
        )
        self._commit()

    def delete_record(self, agent_id: str) -> None:
        """删除 agent 注册记录。"""

        self._execute(
            """
            DELETE FROM agentos_agent_registry
            WHERE agent_id = %s
            """,
            (agent_id,),
        )
        self._commit()

    def load_record(self, agent_id: str) -> AgentRegistryRecord | None:
        """读取 agent 注册记录。"""

        row = self._execute(
            """
            SELECT card, heartbeat_at FROM agentos_agent_registry WHERE agent_id = %s
            """,
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def list_records(self) -> list[AgentRegistryRecord]:
        """列出所有 agent 注册记录。"""

        rows = self._execute(
            """
            SELECT card, heartbeat_at FROM agentos_agent_registry
            ORDER BY agent_id
            """,
        ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def save_affinity(self, affinity: SessionAffinity) -> None:
        """保存 session affinity。"""

        self._execute(
            """
            INSERT INTO agentos_agent_session_affinity
                (session_id, agent_id, expires_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                agent_id = EXCLUDED.agent_id,
                expires_at = EXCLUDED.expires_at,
                updated_at = now()
            """,
            (affinity.session_id, affinity.agent_id, affinity.expires_at),
        )
        self._commit()

    def load_affinity(self, session_id: str) -> SessionAffinity | None:
        """读取 session affinity。"""

        row = self._execute(
            """
            SELECT agent_id, expires_at FROM agentos_agent_session_affinity
            WHERE session_id = %s
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return affinity_from_dict(
            {
                "session_id": session_id,
                "agent_id": row[0],
                "expires_at": row[1],
            },
        )

    def delete_affinity(self, session_id: str) -> None:
        """删除 session affinity。"""

        self._execute(
            """
            DELETE FROM agentos_agent_session_affinity
            WHERE session_id = %s
            """,
            (session_id,),
        )
        self._commit()

    def _record_from_row(self, row: tuple[object, ...]) -> AgentRegistryRecord:
        return AgentRegistryRecord(
            card=card_from_dict(self._json_value(row[0])),
            heartbeat_at=float(row[1]),
        )

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

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            return json.loads(value)
        return dict(value)  # type: ignore[arg-type]
