from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from agentos._sqlite_async import (
    finish_sqlite_operation,
    open_sqlite_connection,
    sqlite_transaction,
)
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
    SessionCheckpoint,
)
from agentos.runtime.durable_commands import (
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.execution import AcceptedTurnExecution
from agentos.runtime.errors import CheckpointConflictError, DurableStoreClosedError
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunAlreadyExistsError, RunState, RunStatus
from agentos.runtime.run_commit import RunTerminalStatus
from agentos.runtime.session import SessionState


_NON_TERMINAL = ("created", "queued", "running", "waiting")


class SQLiteDurableStore:
    """SQLite Run/Command/Checkpoint 真值 Store。"""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._connection: aiosqlite.Connection | None = connection

    @classmethod
    async def open(
        cls,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> SQLiteDurableStore:
        connection = await open_sqlite_connection(
            Path(database_path),
            isolation_level=None,
        )
        try:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA busy_timeout = 30000")
            await initialize_durable_schema(connection)
        except BaseException:
            await finish_sqlite_operation(connection.close)
            raise
        return cls(connection, clock=clock)

    async def __aenter__(self) -> SQLiteDurableStore:
        self._ensure_open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """幂等关闭 SQLite connection。"""

        async with self._lock:
            connection = self._connection
            if connection is None:
                return
            await finish_sqlite_operation(
                connection.close,
                on_success=lambda: setattr(self, "_connection", None),
            )

    async def initialize_session(self, session: SessionState) -> None:
        """幂等初始化 Session 恢复记录。"""

        if type(session) is not SessionState:
            raise TypeError("session must be SessionState")
        async with self._transaction() as connection:
            await connection.execute(
                "INSERT OR IGNORE INTO durable_sessions "
                "(session_id, status, next_turn_number) VALUES (?, ?, ?)",
                (session.id, session.status, session.next_turn_number()),
            )

    async def create(self, state: RunState) -> RunState:
        """创建 Run，并拒绝同 Session 的第二个非终态 Run。"""

        async with self._transaction() as connection:
            if await select_run(connection, state.session_id, state.run_id) is not None:
                raise RunAlreadyExistsError(f"run already exists: {state.run_id}")
            async with connection.execute(
                "SELECT run_id FROM durable_runs WHERE session_id = ? "
                "AND status IN (?, ?, ?, ?)",
                (state.session_id, *_NON_TERMINAL),
            ) as cursor:
                active = await cursor.fetchone()
            if active is not None:
                raise RunAlreadyExistsError(
                    f"session already has an active run: {active['run_id']}",
                )
            await insert_run(connection, state)
        return state

    async def get(self, *, session_id: str, run_id: str) -> RunState | None:
        """按 Session Scope 读取 Run；不存在时返回 None。"""

        async with self._lock:
            row = await select_run(self._ensure_open(), session_id, run_id)
            return None if row is None else row_to_state(row)

    async def transition(
        self,
        *,
        session_id: str,
        run_id: str,
        status: RunStatus,
        wait_reason: WaitReason | None = None,
        guard: RunWriteGuard,
        turn_id: str | None = None,
    ) -> RunState:
        """执行带强制版本检查的非 WAITING Run 状态转换。"""

        if status is RunStatus.WAITING:
            raise CheckpointConflictError(
                "durable WAITING requires atomic commit_waiting",
            )
        async with self._transaction() as connection:
            current = await require_run(connection, session_id, run_id)
            if guard.claim_id is not None:
                raise CheckpointConflictError(
                    "durable sqlite store does not accept fenced writes",
                )
            if current.aggregate_version != guard.expected_version:
                raise CheckpointConflictError(
                    "durable run aggregate version conflict",
                )
            updated = current.transition(status, wait_reason=wait_reason)
            await update_run(connection, updated)
        return updated

    async def commit_running(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        """原子提交 RUNNING 恢复状态并推进 aggregate version。"""

        cursor = checkpoint.execution_cursor
        if cursor is None:
            raise CheckpointConflictError("running checkpoint requires a cursor")
        if cursor.turn_id != turn_id:
            raise CheckpointConflictError(
                "running checkpoint cursor does not match turn_id",
            )
        async with self._transaction() as connection:
            current = await self._require_checkpoint_write(
                connection,
                checkpoint,
                run_id=run_id,
                guard=guard,
            )
            if current.status is not RunStatus.RUNNING:
                raise CheckpointConflictError("running checkpoint requires a running run")
            updated = replace(
                current,
                aggregate_version=current.aggregate_version + 1,
            )
            committed = await self._write_checkpoint_state(
                connection,
                checkpoint,
                run_id=run_id,
                turn_id=turn_id,
                aggregate_version=updated.aggregate_version,
            )
            await update_run(connection, updated)
        return committed

    async def commit_waiting(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        """原子提交恢复状态、checkpoint 与 RUNNING 到 WAITING 转换。"""

        if checkpoint.execution_cursor is not None:
            raise CheckpointConflictError("waiting checkpoint cannot retain a cursor")
        async with self._transaction() as connection:
            current = await self._require_checkpoint_write(
                connection,
                checkpoint,
                run_id=run_id,
                guard=guard,
            )
            updated = current.transition(RunStatus.WAITING, wait_reason=reason)
            committed = await self._write_checkpoint_state(
                connection,
                checkpoint,
                run_id=run_id,
                turn_id=turn_id,
                aggregate_version=updated.aggregate_version,
            )
            await update_run(connection, updated)
        return committed

    async def commit_terminal(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        status: RunTerminalStatus,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        """原子提交恢复状态、清 cursor 并转换 execution 终态。"""

        if checkpoint.execution_cursor is not None:
            raise CheckpointConflictError("terminal checkpoint cannot retain a cursor")
        terminal = RunStatus(status)
        async with self._transaction() as connection:
            current = await self._require_checkpoint_write(
                connection,
                checkpoint,
                run_id=run_id,
                guard=guard,
            )
            updated = current.transition(terminal)
            committed = await self._write_checkpoint_state(
                connection,
                checkpoint,
                run_id=run_id,
                turn_id=turn_id,
                aggregate_version=updated.aggregate_version,
            )
            await update_run(connection, updated)
        return committed

    async def accept_command(
        self,
        *,
        session_id: str,
        command: DurableRunCommand,
        now: datetime,
    ) -> AcceptedTurnExecution | DurableCommandReceipt:
        """原子接受、去重并应用一个 Durable Command。"""

        async with self._transaction() as connection:
            return await accept_command(
                connection,
                session_id=session_id,
                command=command,
                now=now,
            )

    async def load_checkpoint(
        self,
        session_id: str,
    ) -> SessionCheckpoint | None:
        """读取 Session 最新 checkpoint；不存在时返回 None。"""

        async with self._lock:
            return await load_checkpoint(self._ensure_open(), session_id)

    async def load_pending_continuation(
        self,
        *,
        session_id: str,
        run_id: str,
    ) -> AcceptedTurnExecution | None:
        """读取崩溃前已接受且仍为 QUEUED 的 continuation。"""

        async with self._lock:
            return await load_pending_continuation(
                self._ensure_open(),
                session_id=session_id,
                run_id=run_id,
            )

    async def latest_checkpoint(
        self,
        session_id: str,
        run_id: str,
    ) -> RunCheckpoint | None:
        """读取指定 Run 的最新 checkpoint metadata。"""

        async with self._lock:
            return await latest_checkpoint(self._ensure_open(), session_id, run_id)

    async def recover_abandoned_runs(
        self,
        session_id: str,
    ) -> tuple[RunState, ...]:
        """把 Session 中遗留的 RUNNING Run 以 fail-closed 方式标记失败。"""

        async with self._transaction() as connection:
            return await recover_abandoned_running(connection, session_id)

    async def _write_checkpoint_state(
        self,
        connection: aiosqlite.Connection,
        snapshot: SessionCheckpoint,
        *,
        run_id: str,
        turn_id: str,
        aggregate_version: int,
    ) -> RunCheckpoint:
        return await write_checkpoint_state(
            connection,
            snapshot,
            run_id=run_id,
            turn_id=turn_id,
            aggregate_version=aggregate_version,
            created_at=normalize_utc(self._clock()),
            write_context=self._write_context_state,
        )

    async def _require_checkpoint_write(
        self,
        connection: aiosqlite.Connection,
        checkpoint: SessionCheckpoint,
        *,
        run_id: str,
        guard: RunWriteGuard,
    ) -> RunState:
        current = await require_run(connection, checkpoint.session_id, run_id)
        if guard.claim_id is not None:
            raise CheckpointConflictError(
                "durable sqlite store does not accept fenced writes",
            )
        if current.aggregate_version != guard.expected_version:
            raise CheckpointConflictError(
                "durable run aggregate version conflict",
            )
        return current

    async def _write_context_state(
        self,
        connection: aiosqlite.Connection,
        snapshot: SessionCheckpoint,
    ) -> None:
        await write_context_state(connection, snapshot)

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            connection = self._ensure_open()
            async with sqlite_transaction(connection):
                yield connection

    def _ensure_open(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise DurableStoreClosedError("durable store is closed")
        return self._connection
