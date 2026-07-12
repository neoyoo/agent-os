from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from typing import Callable, TypeVar, cast

from agentos.multi.serializers import (
    envelope_from_dict,
    envelope_to_dict,
    team_member_record_from_dict,
    team_member_record_to_dict,
    team_message_from_dict,
    team_message_to_dict,
    team_record_from_dict,
    team_record_to_dict,
    team_ui_event_from_dict,
    team_ui_event_to_dict,
)
from agentos.multi.team import (
    TeamWorkerCancellationRecord,
    TeamWorkerCancellationStore,
    TeamWorkerRetryRecord,
    TeamWorkerRetryStore,
    TeamUiEvent,
    TeamUiEventKind,
    TeamUiStreamStore,
    TeamMessage,
    TeamMemberRecord,
    TeamNotFoundError,
    TeamRecord,
)
from agentos.multi.message_queue import QueueDelivery
from agentos.persistence.postgres import BackendUnavailableError
from agentos.persistence.protocols import PostgresConnection, PostgresCursor
from agentos.multi.team import TeamWorkerCancellationStatus


_F = TypeVar("_F", bound=Callable[..., object])


def _with_connection_scope(method: _F) -> _F:
    @wraps(method)
    def wrapper(self: "_PostgresTeamConnectionLeaseMixin", *args: object, **kwargs: object) -> object:
        with self._connection_scope():
            return method(self, *args, **kwargs)

    return cast(_F, wrapper)


class _PostgresTeamConnectionLeaseMixin:
    _active_connection: ContextVar[object | None]
    _connection: object | None
    _dsn: str
    _owns_pool: bool
    _pool: object | None

    def _configure_connection_boundary(
        self,
        *,
        dsn: str,
        connection: object | None,
        pool: object | None,
        dependency_message: str,
    ) -> None:
        self._active_connection = ContextVar(
            f"{self.__class__.__name__}.active_connection",
            default=None,
        )
        self._connection = None
        self._pool = pool
        self._owns_pool = False
        self._dsn = dsn
        if connection is not None:
            self._connection = connection
            return
        if pool is not None:
            self._ensure_pool_supported(pool)
            return
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(dependency_message) from error
        self._connection = psycopg.connect(dsn)

    def _ensure_pool_supported(self, pool: object) -> None:
        getconn = getattr(pool, "getconn", None)
        connection_method = getattr(pool, "connection", None)
        if callable(getconn) or callable(connection_method):
            return
        raise RuntimeError("Postgres pool must provide getconn() or connection()")

    @contextmanager
    def _connection_scope(self) -> Iterator[object]:
        active_connection = self._active_connection.get()
        if active_connection is not None:
            yield active_connection
            return
        if self._connection is not None:
            token = self._active_connection.set(self._connection)
            try:
                yield self._connection
            finally:
                self._active_connection.reset(token)
            return
        pool = self._pool
        if pool is None:
            raise BackendUnavailableError("Postgres connection is not configured")
        connection, context = self._borrow_pool_connection(pool)
        token = self._active_connection.set(connection)
        try:
            yield connection
        finally:
            self._active_connection.reset(token)
            self._return_pool_connection(pool, connection, context)

    def _borrow_pool_connection(self, pool: object) -> tuple[object, object | None]:
        getconn = getattr(pool, "getconn", None)
        if callable(getconn):
            return getconn(), None
        connection_method = getattr(pool, "connection", None)
        if callable(connection_method):
            context = connection_method()
            return context.__enter__(), context
        raise RuntimeError("Postgres pool must provide getconn() or connection()")

    def _return_pool_connection(
        self,
        pool: object,
        connection: object,
        context: object | None,
    ) -> None:
        if context is not None:
            context.__exit__(None, None, None)
            return
        putconn = getattr(pool, "putconn", None)
        if callable(putconn):
            putconn(connection)
            return
        close = getattr(connection, "close", None)
        if callable(close):
            close()

    def close(self) -> None:
        """Close owned direct connections or owned pools."""

        if self._connection is not None:
            close = getattr(self._connection, "close", None)
            if callable(close):
                close()
            return
        if self._owns_pool and self._pool is not None:
            close = getattr(self._pool, "close", None)
            if callable(close):
                close()

    def _execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresCursor:
        connection = self._active_connection.get()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        try:
            return cast(PostgresConnection, connection).execute(sql, params)
        except Exception as error:
            raise BackendUnavailableError("Postgres backend unavailable") from error

    def _commit(self) -> None:
        connection = self._active_connection.get()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        commit = getattr(connection, "commit", None)
        if commit is not None:
            commit()


class PostgresTeamStore(_PostgresTeamConnectionLeaseMixin):
    """Postgres-backed TeamStore; schema is created by migration."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """Create a Postgres team store."""

        self._configure_connection_boundary(
            dsn=dsn,
            connection=connection,
            pool=pool,
            dependency_message=(
                "PostgresTeamStore requires the optional dependency "
                "`agentos[postgres]`."
            ),
        )

    @classmethod
    def from_pool(cls, dsn: str, pool: object | None = None) -> "PostgresTeamStore":
        """Create a store from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresTeamStore pool support requires `agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
            store = cls(dsn, pool=pool)
            store._owns_pool = True
            return store
        return cls(dsn, pool=pool)

    @_with_connection_scope
    def create_team(self, team: TeamRecord) -> None:
        """Create a team record."""

        self._execute(
            """
            INSERT INTO agentos_team_records (
              team_id, status, leader_agent_id, created_at, payload
            )
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                team.team_id,
                team.status,
                team.leader_agent_id,
                team.created_at,
                self._json_dump(team_record_to_dict(team)),
            ),
        )
        self._commit()

    @_with_connection_scope
    def get_team(self, team_id: str) -> TeamRecord | None:
        """Return a team record."""

        row = self._execute(
            """
            SELECT payload FROM agentos_team_records
            WHERE team_id = %s
            """,
            (team_id,),
        ).fetchone()
        if row is None:
            return None
        return team_record_from_dict(self._json_value(row[0]))

    @_with_connection_scope
    def mark_team_deleted(self, team_id: str, *, now: float) -> bool:
        """Mark a team deleted."""

        current = self.get_team(team_id)
        if current is None:
            return False
        updated = replace(current, status="deleted")
        row = self._execute(
            """
            UPDATE agentos_team_records
            SET status = 'deleted',
                payload = %s::jsonb
            WHERE team_id = %s
            RETURNING payload
            """,
            (
                self._json_dump(team_record_to_dict(updated)),
                team_id,
            ),
        ).fetchone()
        self._commit()
        return row is not None

    @_with_connection_scope
    def add_member(self, member: TeamMemberRecord) -> None:
        """Add or replace a team member record."""

        self._require_active_team(member.team_id)
        self._execute(
            """
            INSERT INTO agentos_team_members (
              team_id, agent_id, role, status, session_id, created_at, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (team_id, agent_id) DO UPDATE SET
                role = EXCLUDED.role,
                status = EXCLUDED.status,
                session_id = EXCLUDED.session_id,
                created_at = EXCLUDED.created_at,
                payload = EXCLUDED.payload
            """,
            (
                member.team_id,
                member.agent_id,
                member.role,
                member.status,
                member.session_id,
                member.created_at,
                self._json_dump(team_member_record_to_dict(member)),
            ),
        )
        self._commit()

    @_with_connection_scope
    def get_member(self, team_id: str, agent_id: str) -> TeamMemberRecord | None:
        """Return one team member."""

        row = self._execute(
            """
            SELECT payload FROM agentos_team_members
            WHERE team_id = %s AND agent_id = %s
            """,
            (team_id, agent_id),
        ).fetchone()
        if row is None:
            return None
        return team_member_record_from_dict(self._json_value(row[0]))

    @_with_connection_scope
    def list_members(self, team_id: str) -> list[TeamMemberRecord]:
        """List team members in deterministic order."""

        rows = self._execute(
            """
            SELECT payload FROM agentos_team_members
            WHERE team_id = %s
            ORDER BY created_at, agent_id
            """,
            (team_id,),
        ).fetchall()
        return [team_member_record_from_dict(self._json_value(row[0])) for row in rows]

    @_with_connection_scope
    def append_message(self, message: TeamMessage) -> None:
        """Append a team message."""

        self._require_active_team(message.team_id)
        self._execute(
            """
            INSERT INTO agentos_team_messages (
              message_id, team_id, from_agent_id, to_agent_id, kind,
              created_at, correlation_id, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                message.message_id,
                message.team_id,
                message.from_agent_id,
                message.to_agent_id,
                message.kind,
                message.created_at,
                message.correlation_id,
                self._json_dump(team_message_to_dict(message)),
            ),
        )
        self._commit()

    @_with_connection_scope
    def list_messages(
        self,
        team_id: str,
        *,
        agent_id: str | None = None,
        after_message_id: str | None = None,
    ) -> list[TeamMessage]:
        """List team messages with optional cursor and visibility filtering."""

        rows = self._execute(
            """
            SELECT payload FROM agentos_team_messages
            WHERE team_id = %s
            ORDER BY created_at, message_id
            """,
            (team_id,),
        ).fetchall()
        messages = [team_message_from_dict(self._json_value(row[0])) for row in rows]
        if after_message_id is not None:
            messages = self._after_message(messages, after_message_id)
        if agent_id is None:
            return messages
        return [
            message
            for message in messages
            if (
                message.to_agent_id is None
                and message.from_agent_id != agent_id
            )
            or message.to_agent_id == agent_id
        ]

    def _require_active_team(self, team_id: str) -> TeamRecord:
        team = self.get_team(team_id)
        if team is None or team.status != "active":
            raise TeamNotFoundError(team_id)
        return team

    def _after_message(
        self,
        messages: list[TeamMessage],
        after_message_id: str,
    ) -> list[TeamMessage]:
        for index, message in enumerate(messages):
            if message.message_id == after_message_id:
                return messages[index + 1 :]
        return messages

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            loaded = json.loads(value)
        else:
            loaded = value
        if not isinstance(loaded, dict):
            raise TypeError("team payload must be an object")
        return loaded


class PostgresTeamWorkerRetryStore(_PostgresTeamConnectionLeaseMixin, TeamWorkerRetryStore):
    """Postgres-backed retry store for team worker continuation deliveries."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """Create a Postgres team worker retry store."""

        self._configure_connection_boundary(
            dsn=dsn,
            connection=connection,
            pool=pool,
            dependency_message=(
                "PostgresTeamWorkerRetryStore requires the optional dependency "
                "`agentos[postgres]`."
            ),
        )

    @classmethod
    def from_pool(
        cls,
        dsn: str,
        pool: object | None = None,
    ) -> "PostgresTeamWorkerRetryStore":
        """Create a retry store from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresTeamWorkerRetryStore pool support requires "
                    "`agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
            store = cls(dsn, pool=pool)
            store._owns_pool = True
            return store
        return cls(dsn, pool=pool)

    @_with_connection_scope
    def get(
        self,
        agent_id: str,
        delivery_id: str,
    ) -> TeamWorkerRetryRecord | None:
        """Return one retry record."""

        row = self._execute(
            """
            SELECT payload FROM agentos_team_worker_retries
            WHERE agent_id = %s AND delivery_id = %s
            """,
            (agent_id, delivery_id),
        ).fetchone()
        if row is None:
            return None
        return self._retry_record_from_dict(self._json_value(row[0]))

    @_with_connection_scope
    def record_failure(self, record: TeamWorkerRetryRecord) -> None:
        """Upsert one retry record."""

        self._execute(
            """
            INSERT INTO agentos_team_worker_retries (
              team_id, agent_id, session_id, delivery_id, status,
              next_run_at, attempts, exhausted_at, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (agent_id, delivery_id) DO UPDATE SET
                team_id = EXCLUDED.team_id,
                session_id = EXCLUDED.session_id,
                status = EXCLUDED.status,
                next_run_at = EXCLUDED.next_run_at,
                attempts = EXCLUDED.attempts,
                exhausted_at = EXCLUDED.exhausted_at,
                payload = EXCLUDED.payload,
                updated_at = now()
            """,
            (
                record.team_id,
                record.agent_id,
                record.session_id,
                record.delivery_id,
                record.status,
                record.next_run_at,
                record.attempts,
                record.exhausted_at,
                self._json_dump(self._retry_record_to_dict(record)),
            ),
        )
        self._commit()

    @_with_connection_scope
    def clear(self, agent_id: str, delivery_id: str) -> None:
        """Clear retry state for one delivery."""

        self._execute(
            """
            DELETE FROM agentos_team_worker_retries
            WHERE agent_id = %s AND delivery_id = %s
            """,
            (agent_id, delivery_id),
        )
        self._commit()

    @_with_connection_scope
    def list_records(
        self,
        team_id: str | None = None,
    ) -> tuple[TeamWorkerRetryRecord, ...]:
        """List retry records ordered by due time and stable identity."""

        if team_id is None:
            rows = self._execute(
                """
                SELECT payload FROM agentos_team_worker_retries
                ORDER BY next_run_at, team_id, agent_id, delivery_id
                """,
            ).fetchall()
        else:
            rows = self._execute(
                """
                SELECT payload FROM agentos_team_worker_retries
                WHERE team_id = %s
                ORDER BY next_run_at, team_id, agent_id, delivery_id
                """,
                (team_id,),
            ).fetchall()
        return tuple(
            self._retry_record_from_dict(self._json_value(row[0]))
            for row in rows
        )

    def _retry_record_to_dict(
        self,
        record: TeamWorkerRetryRecord,
    ) -> dict[str, object]:
        return {
            "team_id": record.team_id,
            "agent_id": record.agent_id,
            "session_id": record.session_id,
            "delivery_id": record.delivery_id,
            "message_id": record.message_id,
            "attempts": record.attempts,
            "status": record.status,
            "next_run_at": record.next_run_at,
            "last_error": record.last_error,
            "delivery": (
                None
                if record.delivery is None
                else {
                    "delivery_id": record.delivery.delivery_id,
                    "envelope": envelope_to_dict(record.delivery.envelope),
                }
            ),
            "exhausted_at": record.exhausted_at,
        }

    def _retry_record_from_dict(
        self,
        data: dict[str, object],
    ) -> TeamWorkerRetryRecord:
        delivery_data = data.get("delivery")
        delivery: QueueDelivery | None = None
        if isinstance(delivery_data, dict):
            envelope_data = delivery_data.get("envelope")
            if isinstance(envelope_data, dict):
                delivery = QueueDelivery(
                    delivery_id=str(delivery_data["delivery_id"]),
                    envelope=envelope_from_dict(envelope_data),
                )
        status = str(data["status"])
        if status not in {"scheduled", "exhausted"}:
            raise ValueError(f"unsupported retry status: {status}")
        return TeamWorkerRetryRecord(
            team_id=str(data["team_id"]),
            agent_id=str(data["agent_id"]),
            session_id=str(data["session_id"]),
            delivery_id=str(data["delivery_id"]),
            message_id=(
                None if data.get("message_id") is None else str(data["message_id"])
            ),
            attempts=int(data["attempts"]),
            status=cast(object, status),  # type: ignore[arg-type]
            next_run_at=float(data["next_run_at"]),
            last_error=str(data["last_error"]),
            delivery=delivery,
            exhausted_at=(
                None
                if data.get("exhausted_at") is None
                else float(data["exhausted_at"])
            ),
        )

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            loaded = json.loads(value)
        else:
            loaded = value
        if not isinstance(loaded, dict):
            raise TypeError("team worker retry payload must be an object")
        return loaded


class PostgresTeamWorkerCancellationStore(
    _PostgresTeamConnectionLeaseMixin,
    TeamWorkerCancellationStore,
):
    """Postgres-backed cancellation store for team worker continuations."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """Create a Postgres team worker cancellation store."""

        self._configure_connection_boundary(
            dsn=dsn,
            connection=connection,
            pool=pool,
            dependency_message=(
                "PostgresTeamWorkerCancellationStore requires the optional "
                "dependency `agentos[postgres]`."
            ),
        )

    @classmethod
    def from_pool(
        cls,
        dsn: str,
        pool: object | None = None,
    ) -> "PostgresTeamWorkerCancellationStore":
        """Create a cancellation store from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresTeamWorkerCancellationStore pool support requires "
                    "`agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
            store = cls(dsn, pool=pool)
            store._owns_pool = True
            return store
        return cls(dsn, pool=pool)

    @_with_connection_scope
    def request_cancel(self, record: TeamWorkerCancellationRecord) -> None:
        """Upsert one cancellation intent."""

        self._execute(
            """
            INSERT INTO agentos_team_worker_cancellations (
              team_id, agent_id, session_id, delivery_id, message_id, status,
              requested_at, acknowledged_at, cleared_at, reason, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (agent_id, session_id, delivery_key, message_key)
            DO UPDATE SET
                team_id = EXCLUDED.team_id,
                delivery_id = EXCLUDED.delivery_id,
                message_id = EXCLUDED.message_id,
                status = EXCLUDED.status,
                requested_at = EXCLUDED.requested_at,
                acknowledged_at = EXCLUDED.acknowledged_at,
                cleared_at = EXCLUDED.cleared_at,
                reason = EXCLUDED.reason,
                payload = EXCLUDED.payload,
                updated_at = now()
            """,
            (
                record.team_id,
                record.agent_id,
                record.session_id,
                record.delivery_id,
                record.message_id,
                record.status,
                record.requested_at,
                record.acknowledged_at,
                record.cleared_at,
                record.reason,
                self._json_dump(self._cancellation_record_to_dict(record)),
            ),
        )
        self._commit()

    @_with_connection_scope
    def match(
        self,
        *,
        team_id: str,
        agent_id: str,
        session_id: str,
        delivery_id: str | None = None,
        message_id: str | None = None,
    ) -> TeamWorkerCancellationRecord | None:
        """Return the best active cancellation intent for a delivery."""

        row = self._execute(
            """
            SELECT payload FROM agentos_team_worker_cancellations
            WHERE team_id = %s
              AND agent_id = %s
              AND session_id = %s
              AND status = 'requested'
              AND (
                (delivery_id IS NOT NULL AND delivery_id = %s)
                OR (message_id IS NOT NULL AND message_id = %s)
                OR (delivery_id IS NULL AND message_id IS NULL)
              )
            ORDER BY
              CASE
                WHEN delivery_id IS NOT NULL OR message_id IS NOT NULL THEN 0
                ELSE 1
              END,
              requested_at,
              delivery_key,
              message_key
            LIMIT 1
            """,
            (team_id, agent_id, session_id, delivery_id, message_id),
        ).fetchone()
        if row is None:
            return None
        return self._cancellation_record_from_dict(self._json_value(row[0]))

    @_with_connection_scope
    def acknowledge(
        self,
        record: TeamWorkerCancellationRecord,
        *,
        now: float,
    ) -> TeamWorkerCancellationRecord:
        """Acknowledge an exact cancellation intent."""

        updated = replace(
            record,
            status="acknowledged",
            acknowledged_at=now,
        )
        self._update_status(updated)
        return updated

    @_with_connection_scope
    def clear(
        self,
        record: TeamWorkerCancellationRecord,
        *,
        now: float,
    ) -> TeamWorkerCancellationRecord:
        """Clear a cancellation intent."""

        updated = replace(record, status="cleared", cleared_at=now)
        self._update_status(updated)
        return updated

    @_with_connection_scope
    def list_records(
        self,
        team_id: str | None = None,
    ) -> tuple[TeamWorkerCancellationRecord, ...]:
        """List cancellation records in deterministic order."""

        if team_id is None:
            rows = self._execute(
                """
                SELECT payload FROM agentos_team_worker_cancellations
                ORDER BY team_id, agent_id, session_id, requested_at,
                         delivery_key, message_key
                """,
            ).fetchall()
        else:
            rows = self._execute(
                """
                SELECT payload FROM agentos_team_worker_cancellations
                WHERE team_id = %s
                ORDER BY team_id, agent_id, session_id, requested_at,
                         delivery_key, message_key
                """,
                (team_id,),
            ).fetchall()
        return tuple(
            self._cancellation_record_from_dict(self._json_value(row[0]))
            for row in rows
        )

    def _update_status(self, record: TeamWorkerCancellationRecord) -> None:
        self._execute(
            """
            UPDATE agentos_team_worker_cancellations
            SET status = %s,
                acknowledged_at = %s,
                cleared_at = %s,
                payload = %s::jsonb,
                updated_at = now()
            WHERE agent_id = %s
              AND session_id = %s
              AND delivery_key = %s
              AND message_key = %s
            """,
            (
                record.status,
                record.acknowledged_at,
                record.cleared_at,
                self._json_dump(self._cancellation_record_to_dict(record)),
                record.agent_id,
                record.session_id,
                self._nullable_key(record.delivery_id),
                self._nullable_key(record.message_id),
            ),
        )
        self._commit()

    def _cancellation_record_to_dict(
        self,
        record: TeamWorkerCancellationRecord,
    ) -> dict[str, object]:
        return {
            "team_id": record.team_id,
            "agent_id": record.agent_id,
            "session_id": record.session_id,
            "reason": record.reason,
            "requested_at": record.requested_at,
            "status": record.status,
            "delivery_id": record.delivery_id,
            "message_id": record.message_id,
            "acknowledged_at": record.acknowledged_at,
            "cleared_at": record.cleared_at,
        }

    def _cancellation_record_from_dict(
        self,
        data: dict[str, object],
    ) -> TeamWorkerCancellationRecord:
        status = str(data["status"])
        if status not in {"requested", "acknowledged", "cleared"}:
            raise ValueError(f"unsupported cancellation status: {status}")
        return TeamWorkerCancellationRecord(
            team_id=str(data["team_id"]),
            agent_id=str(data["agent_id"]),
            session_id=str(data["session_id"]),
            reason=str(data["reason"]),
            requested_at=float(data["requested_at"]),
            status=cast(TeamWorkerCancellationStatus, status),
            delivery_id=(
                None
                if data.get("delivery_id") is None
                else str(data["delivery_id"])
            ),
            message_id=(
                None if data.get("message_id") is None else str(data["message_id"])
            ),
            acknowledged_at=(
                None
                if data.get("acknowledged_at") is None
                else float(data["acknowledged_at"])
            ),
            cleared_at=(
                None
                if data.get("cleared_at") is None
                else float(data["cleared_at"])
            ),
        )

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            loaded = json.loads(value)
        else:
            loaded = value
        if not isinstance(loaded, dict):
            raise TypeError("team worker cancellation payload must be an object")
        return loaded

    def _nullable_key(self, value: str | None) -> str:
        return "null:" if value is None else f"value:{value}"


class PostgresTeamUiStreamStore(_PostgresTeamConnectionLeaseMixin, TeamUiStreamStore):
    """Postgres-backed UI event stream for team projections."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """Create a Postgres team UI stream store."""

        self._configure_connection_boundary(
            dsn=dsn,
            connection=connection,
            pool=pool,
            dependency_message=(
                "PostgresTeamUiStreamStore requires the optional dependency "
                "`agentos[postgres]`."
            ),
        )

    @classmethod
    def from_pool(
        cls,
        dsn: str,
        pool: object | None = None,
    ) -> "PostgresTeamUiStreamStore":
        """Create a UI stream store from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresTeamUiStreamStore pool support requires "
                    "`agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
            store = cls(dsn, pool=pool)
            store._owns_pool = True
            return store
        return cls(dsn, pool=pool)

    @_with_connection_scope
    def append(
        self,
        *,
        team_id: str,
        kind: TeamUiEventKind,
        payload: Mapping[str, object],
        created_at: float,
    ) -> TeamUiEvent:
        """Append a UI event with a monotonic per-team event id."""

        sequence = self._execute(
            """
            INSERT INTO agentos_team_ui_event_sequences (team_id, next_event_id)
            VALUES (%s, 2)
            ON CONFLICT (team_id) DO UPDATE
            SET next_event_id = agentos_team_ui_event_sequences.next_event_id + 1
            RETURNING next_event_id - 1
            """,
            (team_id,),
        ).fetchone()
        if sequence is None:
            raise BackendUnavailableError("Postgres backend unavailable")
        event = TeamUiEvent(
            event_id=int(sequence[0]),
            team_id=team_id,
            kind=kind,
            payload=dict(payload),
            created_at=created_at,
        )
        self._execute(
            """
            INSERT INTO agentos_team_ui_events (
              team_id, event_id, kind, created_at, payload
            )
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                event.team_id,
                event.event_id,
                event.kind,
                event.created_at,
                self._json_dump(team_ui_event_to_dict(event)),
            ),
        )
        self._commit()
        return event

    @_with_connection_scope
    def list_events(
        self,
        team_id: str,
        *,
        after_event_id: int | None = None,
    ) -> tuple[TeamUiEvent, ...]:
        """List UI events for one team after an optional cursor."""

        rows = self._execute(
            """
            SELECT payload FROM agentos_team_ui_events
            WHERE team_id = %s AND event_id > %s
            ORDER BY event_id
            """,
            (team_id, after_event_id or 0),
        ).fetchall()
        return tuple(
            team_ui_event_from_dict(self._json_value(row[0]))
            for row in rows
        )

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            loaded = json.loads(value)
        else:
            loaded = value
        if not isinstance(loaded, dict):
            raise TypeError("team UI event payload must be an object")
        return loaded
