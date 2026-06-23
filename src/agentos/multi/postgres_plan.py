from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from threading import local
from typing import cast

from agentos.multi.planner import (
    PlanClaimRecord,
    PlanClaimResult,
    PlanClaimStore,
    PlanNotFoundError,
    PlanState,
    PlanStoreRecord,
    PlanStore,
)
from agentos.multi.serializers import plan_state_from_dict, plan_state_to_dict
from agentos.persistence.postgres import BackendUnavailableError
from agentos.persistence.protocols import PostgresConnection, PostgresCursor


class _PostgresConnectionLeaseMixin:
    _active_connection: object | None
    _connection: object | None
    _dsn: str
    _owns_pool: bool
    _pool: object | None
    _thread_state: local

    def _configure_connection_boundary(
        self,
        *,
        dsn: str,
        connection: object | None,
        pool: object | None,
        dependency_message: str,
    ) -> None:
        self._active_connection = None
        self._thread_state = local()
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
        active_connection = self._current_active_connection()
        if active_connection is not None:
            yield active_connection
            return
        if self._connection is not None:
            self._set_active_connection(self._connection)
            try:
                yield self._connection
            except BaseException:
                self._rollback()
                raise
            else:
                self._rollback_if_transaction_open()
            finally:
                self._clear_active_connection()
            return
        pool = self._pool
        if pool is None:
            raise BackendUnavailableError("Postgres connection is not configured")
        connection, context = self._borrow_pool_connection(pool)
        self._set_active_connection(connection)
        try:
            yield connection
        except BaseException:
            self._rollback()
            raise
        else:
            self._rollback_if_transaction_open()
        finally:
            self._clear_active_connection()
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
            context.__exit__(*sys.exc_info())
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
        connection = self._current_active_connection()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        try:
            return cast(PostgresConnection, connection).execute(sql, params)
        except Exception as error:
            raise BackendUnavailableError("Postgres backend unavailable") from error

    def _commit(self) -> None:
        connection = self._current_active_connection()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        commit = getattr(connection, "commit", None)
        if commit is not None:
            commit()
        self._mark_transaction_closed()

    def _rollback(self) -> None:
        connection = self._current_active_connection()
        if connection is None:
            raise BackendUnavailableError("Postgres operation has no connection lease")
        rollback = getattr(connection, "rollback", None)
        if rollback is not None:
            rollback()
        self._mark_transaction_closed()

    def _rollback_if_transaction_open(self) -> None:
        if not self._transaction_closed():
            self._rollback()

    def _current_active_connection(self) -> object | None:
        return getattr(self._thread_state, "active_connection", None)

    def _set_active_connection(self, connection: object) -> None:
        self._thread_state.active_connection = connection
        self._thread_state.transaction_closed = False
        self._active_connection = connection

    def _clear_active_connection(self) -> None:
        self._thread_state.active_connection = None
        self._thread_state.transaction_closed = True
        self._active_connection = None

    def _transaction_closed(self) -> bool:
        return bool(getattr(self._thread_state, "transaction_closed", True))

    def _mark_transaction_closed(self) -> None:
        self._thread_state.transaction_closed = True


class PostgresPlanStore(_PostgresConnectionLeaseMixin, PlanStore):
    """Postgres-backed PlanStore; schema is created by migration."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """Create a Postgres plan store."""

        self._configure_connection_boundary(
            dsn=dsn,
            connection=connection,
            pool=pool,
            dependency_message=(
                "PostgresPlanStore requires the optional dependency "
                "`agentos[postgres]`."
            ),
        )

    @classmethod
    def from_pool(
        cls,
        dsn: str,
        pool: object | None = None,
    ) -> "PostgresPlanStore":
        """Create a plan store from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresPlanStore pool support requires `agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
        return cls(dsn, pool=pool)

    def create_plan(self, plan: PlanState) -> None:
        """Create a plan record."""

        with self._connection_scope():
            if self.get_plan(plan.plan_id) is not None:
                raise ValueError(f"plan already exists: {plan.plan_id}")
            self._execute(
                """
                INSERT INTO agentos_plans (
                  plan_id, owner_agent_id, status, created_at, updated_at, payload
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    plan.plan_id,
                    plan.owner_agent_id,
                    plan.status,
                    plan.created_at,
                    plan.updated_at,
                    self._json_dump(plan_state_to_dict(plan)),
                ),
            )
            self._commit()

    def save_plan(self, plan: PlanState) -> None:
        """Save an existing plan record."""

        with self._connection_scope():
            row = self._execute(
                """
                UPDATE agentos_plans
                SET owner_agent_id = %s,
                    status = %s,
                    created_at = %s,
                    updated_at = %s,
                    payload = %s::jsonb,
                    revision = revision + 1,
                    row_updated_at = now()
                WHERE plan_id = %s
                RETURNING payload
                """,
                (
                    plan.owner_agent_id,
                    plan.status,
                    plan.created_at,
                    plan.updated_at,
                    self._json_dump(plan_state_to_dict(plan)),
                    plan.plan_id,
                ),
            ).fetchone()
            if row is None:
                raise PlanNotFoundError(plan.plan_id)
            self._commit()

    def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        """Atomically save a plan only when its revision has not changed."""

        with self._connection_scope():
            row = self._execute(
                """
                UPDATE agentos_plans
                SET owner_agent_id = %s,
                    status = %s,
                    created_at = %s,
                    updated_at = %s,
                    payload = %s::jsonb,
                    revision = revision + 1,
                    row_updated_at = now()
                WHERE plan_id = %s
                  AND revision = %s
                RETURNING payload, revision
                """,
                (
                    plan.owner_agent_id,
                    plan.status,
                    plan.created_at,
                    plan.updated_at,
                    self._json_dump(plan_state_to_dict(plan)),
                    plan.plan_id,
                    int(expected_revision),
                ),
            ).fetchone()
            if row is None:
                if self.get_plan(plan.plan_id) is None:
                    raise PlanNotFoundError(plan.plan_id)
                self._rollback()
                return False
            self._commit()
            return True

    def save_plan_if_claimed(
        self,
        plan: PlanState,
        claim: PlanClaimRecord,
        *,
        expected_revision: int,
        now: float,
    ) -> bool:
        """Atomically save while exact claim and read revision are current."""

        with self._connection_scope():
            row = self._execute(
                """
                UPDATE agentos_plans AS plans
                SET owner_agent_id = %s,
                    status = %s,
                    created_at = %s,
                    updated_at = %s,
                    payload = %s::jsonb,
                    revision = plans.revision + 1,
                    row_updated_at = now()
                WHERE plans.plan_id = %s
                  AND plans.revision = %s
                  AND EXISTS (
                    SELECT 1
                    FROM agentos_plan_claims AS claims
                    WHERE claims.plan_id = plans.plan_id
                      AND claims.owner_agent_id = %s
                      AND claims.worker_id = %s
                      AND claims.claimed_at = %s
                      AND claims.lease_expires_at = %s
                      AND claims.generation = %s
                      AND claims.lease_expires_at > %s
                  )
                RETURNING plans.payload
                """,
                (
                    plan.owner_agent_id,
                    plan.status,
                    plan.created_at,
                    plan.updated_at,
                    self._json_dump(plan_state_to_dict(plan)),
                    plan.plan_id,
                    int(expected_revision),
                    claim.owner_agent_id,
                    claim.worker_id,
                    claim.claimed_at,
                    claim.lease_expires_at,
                    claim.generation,
                    float(now),
                ),
            ).fetchone()
            if row is None:
                if self.get_plan(plan.plan_id) is None:
                    raise PlanNotFoundError(plan.plan_id)
                self._rollback()
                return False
            self._commit()
            return True

    def get_plan(self, plan_id: str) -> PlanState | None:
        """Return one plan."""

        with self._connection_scope():
            row = self._execute(
                """
                SELECT payload FROM agentos_plans
                WHERE plan_id = %s
                """,
                (plan_id,),
            ).fetchone()
            if row is None:
                return None
            return plan_state_from_dict(self._json_value(row[0]))

    def get_plan_record(self, plan_id: str) -> PlanStoreRecord | None:
        """Return one plan with its optimistic concurrency revision."""

        with self._connection_scope():
            row = self._execute(
                """
                SELECT payload, revision FROM agentos_plans
                WHERE plan_id = %s
                """,
                (plan_id,),
            ).fetchone()
            if row is None:
                return None
            return PlanStoreRecord(
                plan=plan_state_from_dict(self._json_value(row[0])),
                revision=int(row[1]),
            )

    def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
        """List plans ordered by update time and id."""

        with self._connection_scope():
            if owner_agent_id is None:
                rows = self._execute(
                    """
                    SELECT payload FROM agentos_plans
                    ORDER BY updated_at, plan_id
                    """,
                ).fetchall()
            else:
                rows = self._execute(
                    """
                    SELECT payload FROM agentos_plans
                    WHERE owner_agent_id = %s
                    ORDER BY updated_at, plan_id
                    """,
                    (owner_agent_id,),
                ).fetchall()
            return [plan_state_from_dict(self._json_value(row[0])) for row in rows]

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            loaded = json.loads(value)
        else:
            loaded = value
        if not isinstance(loaded, dict):
            raise TypeError("plan payload must be an object")
        return loaded


class PostgresPlanClaimStore(_PostgresConnectionLeaseMixin, PlanClaimStore):
    """Postgres-backed claim/lease store for planner scheduler workers."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """Create a Postgres plan claim store."""

        self._configure_connection_boundary(
            dsn=dsn,
            connection=connection,
            pool=pool,
            dependency_message=(
                "PostgresPlanClaimStore requires the optional dependency "
                "`agentos[postgres]`."
            ),
        )

    @classmethod
    def from_pool(
        cls,
        dsn: str,
        pool: object | None = None,
    ) -> "PostgresPlanClaimStore":
        """Create a plan claim store from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresPlanClaimStore pool support requires "
                    "`agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
        return cls(dsn, pool=pool)

    def claim_plan(
        self,
        *,
        plan_id: str,
        owner_agent_id: str,
        worker_id: str,
        lease_seconds: float,
        now: float,
    ) -> PlanClaimResult:
        """Claim or renew a plan lease when it is free or expired."""

        self._validate_non_empty(plan_id, field_name="plan_id")
        self._validate_non_empty(owner_agent_id, field_name="owner_agent_id")
        self._validate_non_empty(worker_id, field_name="worker_id")
        lease_seconds = float(lease_seconds)
        now = float(now)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        lease_expires_at = now + lease_seconds
        with self._connection_scope():
            row = self._execute(
                """
                INSERT INTO agentos_plan_claims (
                  plan_id, owner_agent_id, worker_id, claimed_at, lease_expires_at,
                  generation, payload
                )
                VALUES (%s, %s, %s, %s, %s, 1, %s::jsonb)
                ON CONFLICT (plan_id) DO UPDATE SET
                    owner_agent_id = EXCLUDED.owner_agent_id,
                    worker_id = EXCLUDED.worker_id,
                    claimed_at = EXCLUDED.claimed_at,
                    lease_expires_at = EXCLUDED.lease_expires_at,
                    generation = agentos_plan_claims.generation + 1,
                    payload = jsonb_build_object(
                      'plan_id', EXCLUDED.plan_id,
                      'owner_agent_id', EXCLUDED.owner_agent_id,
                      'worker_id', EXCLUDED.worker_id,
                      'claimed_at', EXCLUDED.claimed_at,
                      'lease_expires_at', EXCLUDED.lease_expires_at,
                      'generation', agentos_plan_claims.generation + 1
                    ),
                    updated_at = now()
                WHERE agentos_plan_claims.lease_expires_at <= %s
                   OR (
                     agentos_plan_claims.owner_agent_id = %s
                     AND agentos_plan_claims.worker_id = %s
                   )
                RETURNING plan_id, owner_agent_id, worker_id, claimed_at,
                          lease_expires_at, generation
                """,
                (
                    plan_id,
                    owner_agent_id,
                    worker_id,
                    now,
                    lease_expires_at,
                    self._json_dump(
                        {
                            "plan_id": plan_id,
                            "owner_agent_id": owner_agent_id,
                            "worker_id": worker_id,
                            "claimed_at": now,
                            "lease_expires_at": lease_expires_at,
                            "generation": 1,
                        },
                    ),
                    now,
                    owner_agent_id,
                    worker_id,
                ),
            ).fetchone()
            if row is None:
                existing_claim = self.get_claim(plan_id)
                self._rollback()
                return PlanClaimResult(
                    status="busy",
                    existing_claim=existing_claim,
                )
            self._commit()
            return PlanClaimResult(status="claimed", claim=self._row_to_claim(row))

    def release_plan(
        self,
        *,
        plan_id: str,
        worker_id: str,
        owner_agent_id: str | None = None,
    ) -> bool:
        """Release the claim for this worker and optional owner only."""

        self._validate_non_empty(plan_id, field_name="plan_id")
        self._validate_non_empty(worker_id, field_name="worker_id")
        if owner_agent_id is not None:
            self._validate_non_empty(owner_agent_id, field_name="owner_agent_id")
        with self._connection_scope():
            if owner_agent_id is None:
                row = self._execute(
                    """
                    DELETE FROM agentos_plan_claims
                    WHERE plan_id = %s AND worker_id = %s
                    RETURNING plan_id
                    """,
                    (plan_id, worker_id),
                ).fetchone()
            else:
                row = self._execute(
                    """
                    DELETE FROM agentos_plan_claims
                    WHERE plan_id = %s
                      AND worker_id = %s
                      AND owner_agent_id = %s
                    RETURNING plan_id
                    """,
                    (plan_id, worker_id, owner_agent_id),
                ).fetchone()
            if row is None:
                self._rollback()
                return False
            self._commit()
            return True

    def expired_claims(
        self,
        *,
        now: float,
        owner_agent_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[PlanClaimRecord, ...]:
        """Return expired claims without mutating the store."""

        now = float(now)
        if owner_agent_id is not None:
            self._validate_non_empty(
                owner_agent_id,
                field_name="owner_agent_id",
            )
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        query_limit = 100 if limit is None else int(limit)
        with self._connection_scope():
            if owner_agent_id is None:
                rows = self._execute(
                    """
                    SELECT plan_id, owner_agent_id, worker_id, claimed_at,
                           lease_expires_at, generation
                    FROM agentos_plan_claims
                    WHERE lease_expires_at <= %s
                    ORDER BY lease_expires_at, plan_id
                    LIMIT %s
                    """,
                    (now, query_limit),
                ).fetchall()
            else:
                rows = self._execute(
                    """
                    SELECT plan_id, owner_agent_id, worker_id, claimed_at,
                           lease_expires_at, generation
                    FROM agentos_plan_claims
                    WHERE lease_expires_at <= %s
                      AND owner_agent_id = %s
                    ORDER BY lease_expires_at, plan_id
                    LIMIT %s
                    """,
                    (now, owner_agent_id, query_limit),
                ).fetchall()
            return tuple(self._row_to_claim(row) for row in rows)

    def release_expired_claim(
        self,
        claim: PlanClaimRecord,
        *,
        now: float,
    ) -> bool:
        """Release the exact expired claim only when it has not changed."""

        now = float(now)
        with self._connection_scope():
            row = self._execute(
                """
                DELETE FROM agentos_plan_claims
                WHERE plan_id = %s
                  AND owner_agent_id = %s
                  AND worker_id = %s
                  AND claimed_at = %s
                  AND lease_expires_at = %s
                  AND generation = %s
                  AND lease_expires_at <= %s
                RETURNING plan_id
                """,
                (
                    claim.plan_id,
                    claim.owner_agent_id,
                    claim.worker_id,
                    claim.claimed_at,
                    claim.lease_expires_at,
                    claim.generation,
                    now,
                ),
            ).fetchone()
            if row is None:
                self._rollback()
                return False
            self._commit()
            return True

    def get_claim(self, plan_id: str) -> PlanClaimRecord | None:
        """Return a claim without evaluating lease expiry."""

        self._validate_non_empty(plan_id, field_name="plan_id")
        with self._connection_scope():
            row = self._execute(
                """
                SELECT plan_id, owner_agent_id, worker_id, claimed_at,
                       lease_expires_at, generation
                FROM agentos_plan_claims
                WHERE plan_id = %s
                """,
                (plan_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_claim(row)

    def _row_to_claim(self, row: tuple[object, ...]) -> PlanClaimRecord:
        return PlanClaimRecord(
            plan_id=str(row[0]),
            owner_agent_id=str(row[1]),
            worker_id=str(row[2]),
            claimed_at=float(row[3]),
            lease_expires_at=float(row[4]),
            generation=int(row[5]),
        )

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _validate_non_empty(self, value: str, *, field_name: str) -> None:
        if not value.strip():
            raise ValueError(f"{field_name} must not be empty")
