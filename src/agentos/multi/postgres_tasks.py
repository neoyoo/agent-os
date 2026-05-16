from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Protocol, Sequence, cast

from agentos.multi.serializers import task_record_from_dict, task_record_to_dict
from agentos.multi.task_store import TaskClaim
from agentos.multi.types import TaskHandle, TaskRecord, TaskResult, TaskStatus
from agentos.persistence.postgres import BackendUnavailableError


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


class PostgresTaskStore:
    """Postgres-backed TaskStore；schema 由 migration 预先创建。"""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """创建 Postgres task store；未安装 postgres extra 时给出清晰错误。"""

        self._pool = pool
        if connection is not None:
            self._connection = connection
            self._dsn = dsn
            return
        if pool is not None:
            getconn = getattr(pool, "getconn", None)
            connection_method = getattr(pool, "connection", None)
            if callable(getconn):
                self._connection = getconn()
            elif callable(connection_method):
                context = connection_method()
                self._connection = context.__enter__()
                self._pool_context = context
            else:
                raise RuntimeError("Postgres pool must provide getconn() or connection()")
            self._dsn = dsn
            return
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgresTaskStore requires the optional dependency "
                "`agentos[postgres]`.",
            ) from error
        self._connection = psycopg.connect(dsn)
        self._dsn = dsn

    @classmethod
    def from_pool(cls, dsn: str, pool: object | None = None) -> "PostgresTaskStore":
        """使用 psycopg_pool ConnectionPool 创建 store。"""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresTaskStore pool support requires `agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
        return cls(dsn, pool=pool)

    def create(self, record: TaskRecord) -> TaskHandle:
        """创建 task record。"""

        self._execute(
            """
            INSERT INTO agentos_multi_agent_tasks (
              task_id, parent_agent_id, target_agent_id, status, worker_id,
              lease_expires_at, deadline_at, version, payload,
              consumed_at, result_notified_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            (
                record.task_id,
                record.parent_agent_id,
                record.target_agent_id,
                record.status,
                record.worker_id,
                record.lease_expires_at,
                record.deadline_at,
                record.version,
                self._json_dump(task_record_to_dict(record)),
                record.consumed_at,
                record.result_notified_at,
                record.updated_at or record.created_at,
            ),
        )
        self._commit()
        return self._handle(record)

    def get(self, task_id: str) -> TaskRecord | None:
        """返回 task record。"""

        row = self._execute(
            """
            SELECT payload FROM agentos_multi_agent_tasks WHERE task_id = %s
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return task_record_from_dict(self._json_value(row[0]))

    def claim_queued(
        self,
        *,
        worker_id: str,
        capabilities: Sequence[str],
        limit: int,
        lease_expires_at: float,
        now: float,
    ) -> list[TaskClaim]:
        """用 Postgres row lock 原子领取 queued 或 expired running tasks。"""

        if limit < 1:
            return []
        rows = self._execute(
            """
            WITH candidates AS (
              SELECT task_id, payload, version
              FROM agentos_multi_agent_tasks
              WHERE (
                status = 'queued'
                OR (
                  status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= %s
                  AND (payload->>'cancel_requested_at') IS NULL
                )
              )
              AND deadline_at > %s
              ORDER BY deadline_at, task_id
              LIMIT %s
              FOR UPDATE SKIP LOCKED
            ),
            patched AS (
              SELECT
                task_id,
                version + 1 AS version,
                payload
                  || jsonb_build_object(
                    'status', 'running',
                    'worker_id', %s::text,
                    'lease_expires_at', %s::double precision,
                    'attempt', COALESCE((payload->>'attempt')::integer, 0) + 1,
                    'updated_at', %s::double precision,
                    'version', version + 1
                  ) AS payload
              FROM candidates
            )
            UPDATE agentos_multi_agent_tasks AS tasks
            SET status = 'running',
                worker_id = %s,
                lease_expires_at = %s,
                version = patched.version,
                updated_at = %s,
                payload = patched.payload
            FROM patched
            WHERE tasks.task_id = patched.task_id
            RETURNING tasks.task_id, tasks.payload
            """,
            (
                now,
                now,
                limit,
                worker_id,
                lease_expires_at,
                now,
                worker_id,
                lease_expires_at,
                now,
            ),
        ).fetchall()
        self._commit()
        claims: list[TaskClaim] = []
        for task_id, payload in rows:
            record = task_record_from_dict(self._json_value(payload))
            claims.append(
                TaskClaim(
                    task_id=str(task_id),
                    worker_id=worker_id,
                    lease_expires_at=lease_expires_at,
                    attempt=record.attempt,
                ),
            )
        return claims

    def mark_running(self, task_id: str, *, now: float | None = None) -> bool:
        """queued -> running。"""

        current = self.get(task_id)
        if current is None or current.status != "queued":
            return False
        return self._transition(
            current,
            replace(
                current,
                status="running",
                updated_at=now or time.time(),
                version=current.version + 1,
            ),
        )

    def mark_completed(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """running -> completed。"""

        return self._terminal_transition(
            task_id,
            status="completed",
            result=result,
            now=now,
            worker_id=worker_id,
            attempt=attempt,
        )

    def mark_failed(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """running -> failed。"""

        return self._terminal_transition(
            task_id,
            status="failed",
            result=result,
            now=now,
            worker_id=worker_id,
            attempt=attempt,
        )

    def request_cancel(self, task_id: str, *, now: float) -> bool:
        """queued 直接取消，running 写入 cancel intent。"""

        current = self.get(task_id)
        if current is None:
            return False
        if current.status == "queued":
            result = TaskResult(
                task_id=task_id,
                status="cancelled",
                summary="task cancelled",
            )
            updated = replace(
                current,
                status="cancelled",
                result=result,
                completed_at=now,
                consumed_at=None,
                updated_at=now,
                version=current.version + 1,
            )
            return self._transition(current, updated, outbox=True)
        if current.status == "running":
            if current.cancel_requested_at is not None:
                return True
            updated = replace(
                current,
                cancel_requested_at=now,
                updated_at=now,
                version=current.version + 1,
            )
            return self._transition(current, updated)
        return current.status == "cancelled"

    def ack_cancelled(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """worker 确认 running task 已取消。"""

        current = self.get(task_id)
        if (
            current is None
            or current.status != "running"
            or current.cancel_requested_at is None
            or not self._claim_matches(current, worker_id=worker_id, attempt=attempt)
        ):
            return False
        updated = replace(
            current,
            status="cancelled",
            result=result,
            completed_at=now,
            consumed_at=None,
            updated_at=now,
            version=current.version + 1,
        )
        return self._transition(current, updated, outbox=True)

    def mark_cancelled(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """queued/running -> cancelled。"""

        return self._terminal_transition(
            task_id,
            status="cancelled",
            result=result,
            now=now,
            worker_id=worker_id,
            attempt=attempt,
            allowed={"queued", "running"},
        )

    def mark_timed_out(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
    ) -> bool:
        """queued/running -> timeout。"""

        return self._terminal_transition(
            task_id,
            status="timeout",
            result=result,
            now=now,
            allowed={"queued", "running"},
            require_claim=False,
        )

    def store_late_result(self, task_id: str, result: TaskResult) -> bool:
        """在 timeout/cancelled 之后保存 late result。"""

        current = self.get(task_id)
        if current is None or current.status not in {"cancelled", "timeout"}:
            return False
        updated = replace(
            current,
            late_result=result,
            updated_at=time.time(),
            version=current.version + 1,
        )
        return self._transition(current, updated)

    def due_timeouts(self, now: float) -> list[TaskRecord]:
        """返回 deadline 已到且仍可标记 timeout 的任务。"""

        rows = self._execute(
            """
            SELECT payload FROM agentos_multi_agent_tasks
            WHERE status IN ('queued', 'running') AND deadline_at <= %s
            ORDER BY deadline_at, task_id
            """,
            (now,),
        ).fetchall()
        return [task_record_from_dict(self._json_value(row[0])) for row in rows]

    def active_for_agent(self, agent_id: str | None = None) -> list[TaskHandle]:
        """返回指定 parent 或全部任务的 handles。"""

        if agent_id is None:
            rows = self._execute(
                """
                SELECT payload FROM agentos_multi_agent_tasks
                ORDER BY task_id
                """,
            ).fetchall()
        else:
            rows = self._execute(
                """
                SELECT payload FROM agentos_multi_agent_tasks
                WHERE parent_agent_id = %s
                ORDER BY task_id
                """,
                (agent_id,),
            ).fetchall()
        return [
            self._handle(task_record_from_dict(self._json_value(row[0])))
            for row in rows
        ]

    def completed_for_agent(self, agent_id: str) -> list[TaskResult]:
        """返回指定 parent 可见的终态 results。"""

        rows = self._execute(
            """
            SELECT payload FROM agentos_multi_agent_tasks
            WHERE parent_agent_id = %s
              AND status IN ('completed', 'failed', 'cancelled', 'timeout')
            ORDER BY task_id
            """,
            (agent_id,),
        ).fetchall()
        results: list[TaskResult] = []
        for row in rows:
            record = task_record_from_dict(self._json_value(row[0]))
            if record.result is not None:
                results.append(record.result)
        return results

    def consume_results_for_agent(self, agent_id: str) -> list[TaskResult]:
        """返回并标记指定 parent 尚未消费的终态 results。"""

        rows = self._execute(
            """
            SELECT payload FROM agentos_multi_agent_tasks
            WHERE parent_agent_id = %s
              AND status IN ('completed', 'failed', 'cancelled', 'timeout')
              AND consumed_at IS NULL
            ORDER BY task_id
            """,
            (agent_id,),
        ).fetchall()
        consumed_at = time.time()
        results: list[TaskResult] = []
        for row in rows:
            current = task_record_from_dict(self._json_value(row[0]))
            if current.result is None:
                continue
            updated = replace(
                current,
                consumed_at=consumed_at,
                updated_at=consumed_at,
                version=current.version + 1,
            )
            if self._transition(current, updated):
                results.append(current.result)
        return results

    def active_count_for_target(self, agent_id: str) -> int:
        """返回指定 target agent 的 queued/running 任务数。"""

        row = self._execute(
            """
            SELECT COUNT(*) FROM agentos_multi_agent_tasks
            WHERE target_agent_id = %s AND status IN ('queued', 'running')
            """,
            (agent_id,),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def mark_result_notified(self, task_id: str, *, now: float) -> bool:
        """标记 terminal result 已发送 result-ready 通知。"""

        current = self.get(task_id)
        if current is None or current.result is None or current.result_notified_at:
            return False
        updated = replace(
            current,
            result_notified_at=now,
            updated_at=now,
            version=current.version + 1,
        )
        return self._transition(current, updated)

    def release_running_leases(
        self,
        *,
        worker_id: str | None = None,
        now: float | None = None,
    ) -> int:
        """shutdown 时释放 running lease，使任务可被重新 claim。"""

        released_at = time.time() if now is None else now
        if worker_id is None:
            rows = self._execute(
                """
                UPDATE agentos_multi_agent_tasks
                SET status = 'queued',
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    version = version + 1,
                    updated_at = %s,
                    payload = payload
                      || jsonb_build_object(
                        'status', 'queued',
                        'worker_id', NULL,
                        'lease_expires_at', NULL,
                        'updated_at', %s::double precision,
                        'version', version + 1
                      )
                WHERE status = 'running'
                RETURNING task_id
                """,
                (released_at, released_at),
            ).fetchall()
        else:
            rows = self._execute(
                """
                UPDATE agentos_multi_agent_tasks
                SET status = 'queued',
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    version = version + 1,
                    updated_at = %s,
                    payload = payload
                      || jsonb_build_object(
                        'status', 'queued',
                        'worker_id', NULL,
                        'lease_expires_at', NULL,
                        'updated_at', %s::double precision,
                        'version', version + 1
                      )
                WHERE status = 'running' AND worker_id = %s
                RETURNING task_id
                """,
                (released_at, released_at, worker_id),
            ).fetchall()
        self._commit()
        return len(rows)

    def pending_outbox(self, *, limit: int) -> list[object]:
        """返回未投递 outbox rows，供 OutboxReconciler 使用。"""

        from agentos.multi.reconciler import OutboxEntry

        rows = self._execute(
            """
            SELECT outbox_id, event_type, payload
            FROM agentos_multi_agent_task_outbox
            WHERE delivered_at IS NULL
            ORDER BY outbox_id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [
            OutboxEntry(
                outbox_id=int(row[0]),
                event_type=str(row[1]),
                record=task_record_from_dict(self._json_value(row[2])),
            )
            for row in rows
        ]

    def mark_outbox_delivered(self, outbox_id: int, *, delivered_at: float) -> bool:
        """标记 outbox row 已投递。"""

        row = self._execute(
            """
            UPDATE agentos_multi_agent_task_outbox
            SET delivered_at = %s
            WHERE outbox_id = %s AND delivered_at IS NULL
            RETURNING outbox_id
            """,
            (delivered_at, outbox_id),
        ).fetchone()
        self._commit()
        return row is not None

    def _terminal_transition(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        result: TaskResult,
        now: float | None,
        worker_id: str | None = None,
        attempt: int | None = None,
        allowed: set[TaskStatus] | None = None,
        require_claim: bool = True,
    ) -> bool:
        current = self.get(task_id)
        allowed_statuses = allowed or {"running"}
        if current is None or current.status not in allowed_statuses:
            return False
        if require_claim and not self._claim_matches(
            current,
            worker_id=worker_id,
            attempt=attempt,
        ):
            return False
        updated_at = time.time() if now is None else now
        updated = replace(
            current,
            status=status,
            result=result,
            completed_at=updated_at,
            consumed_at=None,
            updated_at=updated_at,
            version=current.version + 1,
        )
        return self._transition(current, updated, outbox=True)

    def _transition(
        self,
        current: TaskRecord,
        updated: TaskRecord,
        *,
        outbox: bool = False,
    ) -> bool:
        row = self._execute(
            """
            UPDATE agentos_multi_agent_tasks
            SET status = %s,
                worker_id = %s,
                lease_expires_at = %s,
                version = %s,
                payload = %s::jsonb,
                consumed_at = %s,
                result_notified_at = %s,
                updated_at = %s
            WHERE task_id = %s
              AND version = %s
              AND status = %s
            RETURNING payload
            """,
            (
                updated.status,
                updated.worker_id,
                updated.lease_expires_at,
                updated.version,
                self._json_dump(task_record_to_dict(updated)),
                updated.consumed_at,
                updated.result_notified_at,
                updated.updated_at or updated.created_at,
                current.task_id,
                current.version,
                current.status,
            ),
        ).fetchone()
        if row is None:
            self._commit()
            return False
        if outbox:
            self._insert_outbox(updated)
        self._commit()
        return True

    def _insert_outbox(self, record: TaskRecord) -> None:
        self._execute(
            """
            INSERT INTO agentos_multi_agent_task_outbox
                (task_id, event_type, payload, created_at)
            VALUES (%s, %s, %s::jsonb, %s)
            """,
            (
                record.task_id,
                "result_ready",
                self._json_dump(task_record_to_dict(record)),
                record.completed_at or record.updated_at or time.time(),
            ),
        )

    def _claim_matches(
        self,
        record: TaskRecord,
        *,
        worker_id: str | None,
        attempt: int | None,
    ) -> bool:
        if record.worker_id is not None and worker_id is None:
            return False
        if record.attempt > 0 and attempt is None:
            return False
        if worker_id is not None and record.worker_id != worker_id:
            return False
        if attempt is not None and record.attempt != attempt:
            return False
        return True

    def _handle(self, record: TaskRecord) -> TaskHandle:
        return TaskHandle(
            task_id=record.task_id,
            mode=record.mode,
            target_agent_id=record.target_agent_id,
            status=record.status,
        )

    def _execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresCursor:
        try:
            return cast(PostgresConnection, self._connection).execute(sql, params)
        except Exception as error:
            raise BackendUnavailableError("Postgres backend unavailable") from error

    def _commit(self) -> None:
        commit = getattr(self._connection, "commit", None)
        if commit is not None:
            commit()

    def close(self) -> None:
        """关闭或归还当前 Postgres connection。"""

        pool = getattr(self, "_pool", None)
        if pool is not None:
            putconn = getattr(pool, "putconn", None)
            if callable(putconn):
                putconn(self._connection)
                return
        context = getattr(self, "_pool_context", None)
        if context is not None:
            context.__exit__(None, None, None)
            return
        close = getattr(self._connection, "close", None)
        if callable(close):
            close()

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            return json.loads(value)
        return dict(value)  # type: ignore[arg-type]
