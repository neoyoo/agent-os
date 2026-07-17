from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import sqlite3
from json import JSONDecodeError
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import Any, cast

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
    """使用 SQLite 持久化 PlanState 和 Store revision。"""

    def __init__(self, database_path: str | Path) -> None:
        """打开数据库、初始化独立 Plan 表并持有显式连接生命周期。"""

        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = None
        connection = sqlite3.connect(
            path,
            timeout=5.0,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            initialize_plan_schema(connection)
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    def __enter__(self) -> SQLitePlanStore:
        with self._lock:
            self._require_connection()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """关闭连接；重复调用不会产生副作用。"""

        with self._lock:
            connection = self._connection
            if connection is None:
                return
            self._connection = None
            connection.close()

    def create_plan(self, plan: PlanState) -> None:
        """创建 revision 为零的 Plan，拒绝重复 plan_id。"""

        with self._lock:
            connection = self._require_connection()
            payload = _serialize_plan(plan)
            try:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO agentos_plans (
                            plan_id,
                            owner_agent_id,
                            revision,
                            schema_version,
                            payload_json
                        ) VALUES (?, ?, 0, ?, ?)
                        """,
                        (
                            plan.plan_id,
                            plan.owner_agent_id,
                            SCHEMA_VERSION,
                            payload,
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"plan already exists: {plan.plan_id}") from error

    def save_plan(self, plan: PlanState) -> None:
        """覆盖已有 Plan 并原子增加 revision。"""

        with self._lock:
            connection = self._require_connection()
            payload = _serialize_plan(plan)
            with _immediate_transaction(connection):
                current = _get_plan_record(connection, plan.plan_id)
                if current is None:
                    raise PlanNotFoundError(plan.plan_id)
                cursor = connection.execute(
                    """
                    UPDATE agentos_plans
                    SET owner_agent_id = ?,
                        revision = revision + 1,
                        payload_json = ?
                    WHERE plan_id = ?
                    """,
                    (plan.owner_agent_id, payload, plan.plan_id),
                )
                if cursor.rowcount == 0:
                    raise PlanNotFoundError(plan.plan_id)

    def get_plan(self, plan_id: str) -> PlanState | None:
        """按 plan_id 读取 Plan，不存在时返回 None。"""

        record = self.get_plan_record(plan_id)
        return None if record is None else record.plan

    def get_plan_record(self, plan_id: str) -> PlanStoreRecord | None:
        """读取 Plan 及其 Store revision。"""

        with self._lock:
            connection = self._require_connection()
            return _get_plan_record(connection, plan_id)

    def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
        """按创建顺序列出 Plan，可按 owner_agent_id 过滤。"""

        with self._lock:
            connection = self._require_connection()
            if owner_agent_id is None:
                rows = connection.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM agentos_plans ORDER BY sequence",
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM agentos_plans "
                    "WHERE owner_agent_id = ? ORDER BY sequence",
                    (owner_agent_id,),
                ).fetchall()
        return [_record_from_row(row).plan for row in rows]

    def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        """仅当 revision 未变化时保存并增加 revision。"""

        with self._lock:
            connection = self._require_connection()
            payload = _serialize_plan(plan)
            with _immediate_transaction(connection):
                current = _get_plan_record(connection, plan.plan_id)
                if current is None:
                    raise PlanNotFoundError(plan.plan_id)
                if current.revision != expected_revision:
                    return False
                cursor = connection.execute(
                    """
                    UPDATE agentos_plans
                    SET owner_agent_id = ?,
                        revision = revision + 1,
                        payload_json = ?
                    WHERE plan_id = ? AND revision = ?
                    """,
                    (
                        plan.owner_agent_id,
                        payload,
                        plan.plan_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount == 1:
                    return True
                raise PlanError(f"failed to update plan revision: {plan.plan_id}")

    def _require_connection(self) -> sqlite3.Connection:
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


def _get_plan_record(
    connection: sqlite3.Connection,
    plan_id: str,
) -> PlanStoreRecord | None:
    row = connection.execute(
        f"SELECT {_SELECT_COLUMNS} FROM agentos_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    return None if row is None else _record_from_row(row)


@contextmanager
def _immediate_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except BaseException:
            pass
        raise


def _record_from_row(row: sqlite3.Row) -> PlanStoreRecord:
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
