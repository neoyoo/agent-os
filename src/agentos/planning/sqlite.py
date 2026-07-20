from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from json import JSONDecodeError
from pathlib import Path
from typing import Any, cast

import aiosqlite

from agentos._sqlite_async import (
    finish_sqlite_operation,
    open_sqlite_connection,
    sqlite_transaction,
)
from agentos.planning.errors import PlanError, PlanNotFoundError
from agentos.planning.models import PlanState
from agentos.planning.serializers import plan_state_from_dict, plan_state_to_dict
from agentos.planning.sqlite_errors import (
    SQLitePlanStoreClosedError,
    SQLitePlanStoreCorruptedError,
    SQLitePlanStoreUnsafeError,
)
from agentos.planning.sqlite_safety import validate_durable_plan
from agentos.planning.sqlite_schema import SCHEMA_VERSION, initialize_plan_schema
from agentos.planning.store import PlanStoreRecord


_SELECT_COLUMNS = "plan_id, owner_agent_id, revision, schema_version, payload_json"


class SQLitePlanStore:
    """使用原生异步 SQLite 持久化 PlanState 与 Store revision。"""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._lock = asyncio.Lock()
        self._connection: aiosqlite.Connection | None = connection

    @classmethod
    async def open(cls, database_path: str | Path) -> SQLitePlanStore:
        connection = await open_sqlite_connection(database_path)
        try:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA busy_timeout = 30000")
            await initialize_plan_schema(connection)
        except BaseException:
            await finish_sqlite_operation(connection.close)
            raise
        return cls(connection)

    async def __aenter__(self) -> SQLitePlanStore:
        self._require_connection()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """幂等关闭连接；关闭失败时保留连接以供重试。"""

        async with self._lock:
            connection = self._connection
            if connection is None:
                return
            await finish_sqlite_operation(
                connection.close,
                on_success=lambda: setattr(self, "_connection", None),
            )

    async def create_plan(self, plan: PlanState) -> None:
        """创建 revision 为零的 Plan，并拒绝重复 plan_id。"""

        payload = _serialize_plan(plan)
        try:
            async with self._transaction() as connection:
                await connection.execute(
                    """
                    INSERT INTO agentos_plans (
                        plan_id, owner_agent_id, revision,
                        schema_version, payload_json
                    ) VALUES (?, ?, 0, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        plan.owner_agent_id,
                        SCHEMA_VERSION,
                        payload,
                    ),
                )
        except aiosqlite.IntegrityError:
            raise ValueError(f"plan already exists: {plan.plan_id}") from None

    async def save_plan(self, plan: PlanState) -> None:
        """覆盖已有 Plan 并原子增加 revision。"""

        payload = _serialize_plan(plan)
        async with self._transaction() as connection:
            current = await _get_plan_record(connection, plan.plan_id)
            if current is None:
                raise PlanNotFoundError(plan.plan_id)
            cursor = await connection.execute(
                """
                UPDATE agentos_plans
                SET owner_agent_id = ?, revision = revision + 1, payload_json = ?
                WHERE plan_id = ?
                """,
                (plan.owner_agent_id, payload, plan.plan_id),
            )
            try:
                if cursor.rowcount == 0:
                    raise PlanNotFoundError(plan.plan_id)
            finally:
                await cursor.close()

    async def get_plan(self, plan_id: str) -> PlanState | None:
        record = await self.get_plan_record(plan_id)
        return None if record is None else record.plan

    async def get_plan_record(self, plan_id: str) -> PlanStoreRecord | None:
        async with self._lock:
            return await _get_plan_record(self._require_connection(), plan_id)

    async def list_plans(
        self,
        owner_agent_id: str | None = None,
    ) -> list[PlanState]:
        async with self._lock:
            connection = self._require_connection()
            if owner_agent_id is None:
                query = f"SELECT {_SELECT_COLUMNS} FROM agentos_plans ORDER BY sequence"
                parameters: tuple[object, ...] = ()
            else:
                query = (
                    f"SELECT {_SELECT_COLUMNS} FROM agentos_plans "
                    "WHERE owner_agent_id = ? ORDER BY sequence"
                )
                parameters = (owner_agent_id,)
            async with connection.execute(query, parameters) as cursor:
                rows = await cursor.fetchall()
        return [_record_from_row(row).plan for row in rows]

    async def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        """仅当 revision 未变化时保存并增加 revision。"""

        payload = _serialize_plan(plan)
        async with self._transaction() as connection:
            current = await _get_plan_record(connection, plan.plan_id)
            if current is None:
                raise PlanNotFoundError(plan.plan_id)
            if current.revision != expected_revision:
                return False
            cursor = await connection.execute(
                """
                UPDATE agentos_plans
                SET owner_agent_id = ?, revision = revision + 1, payload_json = ?
                WHERE plan_id = ? AND revision = ?
                """,
                (
                    plan.owner_agent_id,
                    payload,
                    plan.plan_id,
                    expected_revision,
                ),
            )
            try:
                if cursor.rowcount == 1:
                    return True
            finally:
                await cursor.close()
            raise PlanError(f"failed to update plan revision: {plan.plan_id}")

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            connection = self._require_connection()
            async with sqlite_transaction(connection):
                yield connection

    def _require_connection(self) -> aiosqlite.Connection:
        connection = self._connection
        if connection is None:
            raise SQLitePlanStoreClosedError("SQLitePlanStore is closed")
        return connection


def _serialize_plan(plan: PlanState) -> str:
    validate_durable_plan(plan)
    normalized = plan_state_from_dict(plan_state_to_dict(plan))
    return json.dumps(
        plan_state_to_dict(normalized),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


async def _get_plan_record(
    connection: aiosqlite.Connection,
    plan_id: str,
) -> PlanStoreRecord | None:
    async with connection.execute(
        f"SELECT {_SELECT_COLUMNS} FROM agentos_plans WHERE plan_id = ?",
        (plan_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return None if row is None else _record_from_row(row)


def _record_from_row(row: aiosqlite.Row) -> PlanStoreRecord:
    try:
        plan_id = row["plan_id"]
        owner_agent_id = row["owner_agent_id"]
        revision = row["revision"]
        schema_version = row["schema_version"]
        payload = row["payload_json"]
        if not isinstance(plan_id, str) or not isinstance(owner_agent_id, str):
            raise ValueError("invalid Plan identity columns")
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid Plan revision")
        if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported Plan schema version")
        if not isinstance(payload, str):
            raise ValueError("invalid Plan payload")
        plan = _deserialize_plan(payload)
        if plan.plan_id != plan_id or plan.owner_agent_id != owner_agent_id:
            raise ValueError("Plan row identity does not match payload")
        return PlanStoreRecord(plan=plan, revision=revision)
    except (
        JSONDecodeError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
        OverflowError,
        SQLitePlanStoreUnsafeError,
    ):
        raise SQLitePlanStoreCorruptedError("stored plan is corrupted") from None


def _deserialize_plan(payload: str) -> PlanState:
    data = json.loads(payload, parse_constant=_reject_json_constant)
    if not isinstance(data, dict):
        raise TypeError("Plan payload root must be an object")
    plan = plan_state_from_dict(cast(dict[str, Any], data))
    if _serialize_plan(plan) != payload:
        raise ValueError("Plan payload is not canonical")
    return plan


def _reject_json_constant(value: str) -> None:
    raise ValueError("invalid JSON constant")


__all__ = [
    "SQLitePlanStore",
    "SQLitePlanStoreClosedError",
    "SQLitePlanStoreCorruptedError",
    "SQLitePlanStoreUnsafeError",
]
