from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from weakref import WeakValueDictionary

from agentos._waiting import WaitReason
from agentos.durable.schema import initialize_durable_schema
from agentos.durable.sqlite_checkpoint import (
    latest_checkpoint,
    load_checkpoint,
    write_checkpoint_state,
    write_context_state,
)
from agentos.durable.sqlite_commands import (
    accept_command,
    load_pending_continuation,
)
from agentos.durable.sqlite_records import (
    insert_run,
    normalize_utc,
    recover_abandoned_running,
    require_run,
    row_to_state,
    select_run,
    update_run,
)
from agentos.runtime.checkpoint import (
    RunCheckpoint,
    RuntimeCheckpointSource,
    SessionCheckpoint,
)
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.errors import CheckpointConflictError, DurableStoreClosedError
from agentos.runtime.run_state import RunAlreadyExistsError, RunState, RunStatus
from agentos.runtime.session import SessionState


_NON_TERMINAL = ("created", "queued", "running", "waiting")


class SQLiteDurableStore:
    """SQLite Run/Command/Checkpoint 真值 Store。"""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self._sources: WeakValueDictionary[str, RuntimeCheckpointSource] = (
            WeakValueDictionary()
        )
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            str(database_path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        try:
            initialize_durable_schema(self._connection)
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> SQLiteDurableStore:
        self._ensure_open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """幂等关闭 SQLite connection。"""

        with self._lock:
            connection, self._connection = self._connection, None
            self._sources.clear()
            if connection is not None:
                connection.close()

    def initialize_session(self, session: SessionState) -> None:
        """幂等初始化 Session 恢复记录。"""

        if type(session) is not SessionState:
            raise TypeError("session must be SessionState")
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO durable_sessions "
                "(session_id, status, next_turn_number) VALUES (?, ?, ?)",
                (session.id, session.status, session.next_turn_number()),
            )

    def bind_checkpoint_source(
        self,
        session_id: str,
        source: RuntimeCheckpointSource,
    ) -> None:
        """为 Session 弱绑定唯一存活的 checkpoint source。"""

        if source.session.id != session_id:
            raise ValueError("checkpoint source belongs to another session")
        with self._lock:
            self._ensure_open()
            existing = self._sources.get(session_id)
            if existing is not None and existing is not source:
                raise CheckpointConflictError(
                    "checkpoint source is already bound",
                )
            self._sources[session_id] = source

    def create(self, state: RunState) -> RunState:
        """创建 Run，并拒绝同 Session 的第二个非终态 Run。"""

        with self._transaction() as connection:
            if select_run(connection, state.session_id, state.run_id) is not None:
                raise RunAlreadyExistsError(f"run already exists: {state.run_id}")
            active = connection.execute(
                "SELECT run_id FROM durable_runs WHERE session_id = ? "
                "AND status IN (?, ?, ?, ?)",
                (state.session_id, *_NON_TERMINAL),
            ).fetchone()
            if active is not None:
                raise RunAlreadyExistsError(
                    f"session already has an active run: {active['run_id']}",
                )
            insert_run(connection, state)
        return state

    def get(self, *, session_id: str, run_id: str) -> RunState | None:
        """按 Session Scope 读取 Run；不存在时返回 None。"""

        with self._lock:
            row = select_run(self._ensure_open(), session_id, run_id)
            return None if row is None else row_to_state(row)

    def transition(
        self,
        *,
        session_id: str,
        run_id: str,
        status: RunStatus,
        wait_reason: WaitReason | None = None,
        expected_version: int | None = None,
        turn_id: str | None = None,
    ) -> RunState:
        """执行带可选版本检查的非 WAITING Run 状态转换。"""

        if status is RunStatus.WAITING:
            raise CheckpointConflictError(
                "durable WAITING requires atomic commit_waiting",
            )
        source = self._checkpoint_source(session_id)
        snapshot = (
            source.capture()
            if source is not None
            and turn_id is not None
            and status
            in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
            else None
        )
        with self._transaction() as connection:
            current = require_run(connection, session_id, run_id)
            if (
                expected_version is not None
                and current.aggregate_version != expected_version
            ):
                raise CheckpointConflictError(
                    "durable run aggregate version conflict",
                )
            updated = current.transition(status, wait_reason=wait_reason)
            if snapshot is not None:
                self._write_checkpoint_state(
                    connection,
                    snapshot,
                    run_id=run_id,
                    turn_id=turn_id,
                    aggregate_version=updated.aggregate_version,
                )
            update_run(connection, updated)
        return updated

    def commit_waiting(
        self,
        *,
        session_id: str,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        expected_version: int,
    ) -> RunCheckpoint:
        """原子提交恢复状态、checkpoint 与 RUNNING 到 WAITING 转换。"""

        source = self._checkpoint_source(session_id)
        if source is None:
            raise CheckpointConflictError("checkpoint source is not bound")
        snapshot = source.capture()
        with self._transaction() as connection:
            current = require_run(connection, session_id, run_id)
            if current.aggregate_version != expected_version:
                raise CheckpointConflictError(
                    "durable run aggregate version conflict",
                )
            updated = current.transition(RunStatus.WAITING, wait_reason=reason)
            checkpoint = self._write_checkpoint_state(
                connection,
                snapshot,
                run_id=run_id,
                turn_id=turn_id,
                aggregate_version=updated.aggregate_version,
            )
            update_run(connection, updated)
        return checkpoint

    def accept_command(
        self,
        *,
        session_id: str,
        command: DurableRunCommand,
        now: datetime,
    ) -> AcceptedContinuationInput | DurableCommandReceipt:
        """原子接受、去重并应用一个 Durable Command。"""

        with self._transaction() as connection:
            return accept_command(
                connection,
                session_id=session_id,
                command=command,
                now=now,
            )

    def load_checkpoint(self, session_id: str) -> SessionCheckpoint | None:
        """读取 Session 最新 checkpoint；不存在时返回 None。"""

        with self._lock:
            return load_checkpoint(self._ensure_open(), session_id)

    def load_pending_continuation(
        self,
        *,
        session_id: str,
        run_id: str,
    ) -> AcceptedContinuationInput | None:
        """读取崩溃前已接受且仍为 QUEUED 的 continuation。"""

        with self._lock:
            return load_pending_continuation(
                self._ensure_open(),
                session_id=session_id,
                run_id=run_id,
            )

    def latest_checkpoint(
        self,
        session_id: str,
        run_id: str,
    ) -> RunCheckpoint | None:
        """读取指定 Run 的最新 checkpoint metadata。"""

        with self._lock:
            return latest_checkpoint(self._ensure_open(), session_id, run_id)

    def recover_abandoned_runs(self, session_id: str) -> tuple[RunState, ...]:
        """把 Session 中遗留的 RUNNING Run 以 fail-closed 方式标记失败。"""

        with self._transaction() as connection:
            return recover_abandoned_running(connection, session_id)

    def _write_checkpoint_state(
        self,
        connection: sqlite3.Connection,
        snapshot: SessionCheckpoint,
        *,
        run_id: str,
        turn_id: str,
        aggregate_version: int,
    ) -> RunCheckpoint:
        return write_checkpoint_state(
            connection,
            snapshot,
            run_id=run_id,
            turn_id=turn_id,
            aggregate_version=aggregate_version,
            created_at=normalize_utc(self._clock()),
            write_context=self._write_context_state,
        )

    def _checkpoint_source(
        self,
        session_id: str,
    ) -> RuntimeCheckpointSource | None:
        with self._lock:
            self._ensure_open()
            return self._sources.get(session_id)

    def _write_context_state(
        self,
        connection: sqlite3.Connection,
        snapshot: SessionCheckpoint,
    ) -> None:
        write_context_state(connection, snapshot)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._ensure_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                try:
                    connection.rollback()
                except BaseException:
                    pass
                raise

    def _ensure_open(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DurableStoreClosedError("durable store is closed")
        return self._connection
