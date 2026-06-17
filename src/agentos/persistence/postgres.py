from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from json import JSONDecodeError
from typing import Callable, Sequence, TypeVar, cast

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
from agentos.persistence.base import (
    SessionSnapshot,
    SessionSnapshotRecord,
    SnapshotConflictError,
    SnapshotLoadError,
    SnapshotVersionError,
)
from agentos.persistence.protocols import PostgresConnection, PostgresCursor
from agentos.persistence.serializers import (
    session_snapshot_from_dict,
    session_snapshot_to_dict,
)
from agentos.runtime.session import SessionState


_F = TypeVar("_F", bound=Callable[..., object])


class BackendUnavailableError(RuntimeError):
    """生产后端连接不可用。"""


class _PostgresConnectionLeaseMixin:
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
        if self._connection is not None:
            if active_connection is not None:
                yield active_connection
                return
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

    def _execute(
        self,
        sql: str,
        params: tuple[object, ...] | None = None,
    ) -> PostgresCursor:
        connection = self._active_connection.get()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        try:
            return cast(PostgresConnection, connection).execute(sql, params or ())
        except Exception as error:
            raise BackendUnavailableError("Postgres backend unavailable") from error

    def _commit(self) -> None:
        connection = self._active_connection.get()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        commit = getattr(connection, "commit", None)
        if commit is not None:
            commit()

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


class PostgresSessionSnapshotPersistence(_PostgresConnectionLeaseMixin):
    """Postgres-backed SessionPersistence adapter for full snapshots."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """Create Postgres snapshot persistence."""

        self._configure_connection_boundary(
            dsn=dsn,
            connection=connection,
            pool=pool,
            dependency_message=(
                "PostgresSessionSnapshotPersistence requires the optional "
                "dependency `agentos[postgres]`."
            ),
        )

    @classmethod
    def from_pool(
        cls,
        dsn: str,
        pool: object | None = None,
    ) -> "PostgresSessionSnapshotPersistence":
        """Create snapshot persistence from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "Postgres pool support requires the optional dependency "
                    "`agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
        return cls(dsn, pool=pool)

    @property
    def backend_dsn(self) -> str:
        """Return the Postgres DSN."""

        return self._dsn

    def save(self, snapshot: SessionSnapshot) -> None:
        """Save the latest full session snapshot."""

        with self._connection_scope():
            self._save(snapshot)

    def load(self, session_id: str) -> SessionSnapshot:
        """Load one full session snapshot."""

        with self._connection_scope():
            row = self._execute(
                """
                SELECT payload FROM agentos_session_snapshots
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            try:
                return session_snapshot_from_dict(self._json_value(row[0]))
            except SnapshotVersionError:
                raise
            except (JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise SnapshotLoadError(
                    f"failed to load snapshot {session_id!r}: {error}",
                ) from error

    def load_record(self, session_id: str) -> SessionSnapshotRecord:
        """Load one full session snapshot with backend mutation revision."""

        with self._connection_scope():
            row = self._execute(
                """
                SELECT revision, payload, lease_fence FROM agentos_session_snapshots
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            try:
                return SessionSnapshotRecord(
                    snapshot=session_snapshot_from_dict(self._json_value(row[1])),
                    revision=int(row[0]),
                    lease_fence=int(row[2]),
                )
            except SnapshotVersionError:
                raise
            except (JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise SnapshotLoadError(
                    f"failed to load snapshot {session_id!r}: {error}",
                ) from error

    def save_if_unchanged(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int,
    ) -> SessionSnapshotRecord:
        """Save snapshot only if the backend revision still matches."""

        if expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        with self._connection_scope():
            return self._save(snapshot, expected_revision=expected_revision)

    def save_if_lease_owned(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int,
        lease: object,
        lease_store: object,
    ) -> SessionSnapshotRecord:
        """Save snapshot only if revision and session lease ownership still match."""

        session_id = snapshot.session_state.id
        lease_session_id = getattr(lease, "session_id", None)
        if lease_session_id != session_id:
            from agentos.channels.durable_session import SessionLeaseError

            raise SessionLeaseError(
                f"session lease mismatch: {lease_session_id!r} != {session_id!r}",
            )
        if expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        ensure_owned = getattr(lease_store, "ensure_owned", None)
        if not callable(ensure_owned):
            raise BackendUnavailableError("session lease store cannot verify ownership")
        with self._connection_scope():
            ensure_owned(lease)
            return self._save(
                snapshot,
                expected_revision=expected_revision,
                lease_fence=int(getattr(lease, "fence", 0)),
            )

    def list_ids(self) -> list[str]:
        """List saved session ids."""

        with self._connection_scope():
            rows = self._execute(
                """
                SELECT session_id FROM agentos_session_snapshots
                ORDER BY session_id
                """,
            ).fetchall()
            return [str(row[0]) for row in rows]

    def delete(self, session_id: str) -> None:
        """Delete one full session snapshot."""

        with self._connection_scope():
            self._execute(
                """
                DELETE FROM agentos_session_snapshots
                WHERE session_id = %s
                """,
                (session_id,),
            )
            self._commit()

    def _save(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int | None = None,
        lease_fence: int | None = None,
    ) -> SessionSnapshotRecord:
        payload = session_snapshot_to_dict(snapshot)
        if expected_revision is None:
            row = self._execute(
                """
                INSERT INTO agentos_session_snapshots
                    (session_id, version, revision, payload, lease_fence)
                VALUES (%s, %s, 1, %s::jsonb, COALESCE(%s, 0))
                ON CONFLICT (session_id) DO UPDATE SET
                    version = EXCLUDED.version,
                    revision = agentos_session_snapshots.revision + 1,
                    payload = EXCLUDED.payload,
                    lease_fence = GREATEST(
                        agentos_session_snapshots.lease_fence,
                        EXCLUDED.lease_fence
                    ),
                    updated_at = now()
                RETURNING revision, lease_fence
                """,
                (
                    snapshot.session_state.id,
                    snapshot.version,
                    json.dumps(payload, ensure_ascii=False),
                    lease_fence,
                ),
            ).fetchone()
        elif expected_revision == 0:
            row = self._execute(
                """
                INSERT INTO agentos_session_snapshots
                    (session_id, version, revision, payload, lease_fence)
                VALUES (%s, %s, 1, %s::jsonb, COALESCE(%s, 0))
                ON CONFLICT (session_id) DO NOTHING
                RETURNING revision, lease_fence
                """,
                (
                    snapshot.session_state.id,
                    snapshot.version,
                    json.dumps(payload, ensure_ascii=False),
                    lease_fence,
                ),
            ).fetchone()
        else:
            row = self._execute(
                """
                UPDATE agentos_session_snapshots
                SET
                    version = %s,
                    revision = revision + 1,
                    payload = %s::jsonb,
                    lease_fence = GREATEST(lease_fence, COALESCE(%s, lease_fence)),
                    updated_at = now()
                WHERE session_id = %s
                  AND revision = %s
                  AND COALESCE(%s, lease_fence) >= lease_fence
                RETURNING revision, lease_fence
                """,
                (
                    snapshot.version,
                    json.dumps(payload, ensure_ascii=False),
                    lease_fence,
                    snapshot.session_state.id,
                    expected_revision,
                    lease_fence,
                ),
            ).fetchone()
        if row is None:
            raise SnapshotConflictError(
                f"snapshot revision conflict: {snapshot.session_state.id}",
            )
        self._commit()
        return SessionSnapshotRecord(
            snapshot=snapshot,
            revision=int(row[0]),
            lease_fence=int(row[1]) if len(row) > 1 else 0,
        )

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            loaded = json.loads(value)
        else:
            loaded = value
        if not isinstance(loaded, dict):
            raise TypeError("snapshot payload must be an object")
        return loaded


def _with_postgres_connection_scope(method: _F) -> _F:
    @wraps(method)
    def wrapper(
        self: _PostgresConnectionLeaseMixin,
        *args: object,
        **kwargs: object,
    ) -> object:
        with self._connection_scope():
            return method(self, *args, **kwargs)

    return cast(_F, wrapper)


class PostgresDurableSessionStore(_PostgresConnectionLeaseMixin):
    """Postgres-backed DurableSessionStore adapter。"""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """创建 Postgres durable store；未安装 postgres extra 时给出清晰错误。"""

        self._configure_connection_boundary(
            dsn=dsn,
            connection=connection,
            pool=pool,
            dependency_message=(
                "PostgresDurableSessionStore requires the optional dependency "
                "`agentos[postgres]`."
            ),
        )

    @classmethod
    def from_pool(cls, dsn: str, pool: object | None = None) -> "PostgresDurableSessionStore":
        """使用 psycopg_pool ConnectionPool 创建 store。"""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "Postgres pool support requires the optional dependency "
                    "`agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
            store = cls(dsn, pool=pool)
            store._owns_pool = True
            return store
        return cls(dsn, pool=pool)

    @_with_postgres_connection_scope
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

    @_with_postgres_connection_scope
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

    @_with_postgres_connection_scope
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

    @_with_postgres_connection_scope
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

    @_with_postgres_connection_scope
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

    @_with_postgres_connection_scope
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

    @_with_postgres_connection_scope
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

    @_with_postgres_connection_scope
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

    @_with_postgres_connection_scope
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
        connection = self._active_connection.get()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        try:
            return cast(PostgresConnection, connection).execute(sql, params or ())
        except Exception as error:
            raise BackendUnavailableError("Postgres backend unavailable") from error

    def _commit(self) -> None:
        connection = self._active_connection.get()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        commit = getattr(connection, "commit", None)
        if commit is not None:
            commit()

    def close(self) -> None:
        """关闭或归还当前 Postgres connection。"""

        if self._connection is None:
            if self._owns_pool and self._pool is not None:
                close = getattr(self._pool, "close", None)
                if callable(close):
                    close()
            return
        close = getattr(self._connection, "close", None)
        if callable(close):
            close()

    def _json_value(self, value: object) -> object:
        """兼容 psycopg JSONB dict/list 返回值和测试 fake 的 JSON str。"""

        if isinstance(value, str):
            return json.loads(value)
        return value
