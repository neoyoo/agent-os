from __future__ import annotations

from datetime import timedelta

from agentos._waiting import WaitReason
from agentos.distributed.errors import CheckpointConflictError
from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from agentos.distributed.models import (
    RequestScope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
)
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres._checkpoints import (
    commit_running as _commit_running,
    commit_terminal as _commit_terminal,
    commit_waiting as _commit_waiting,
    latest_checkpoint as _latest_checkpoint,
    load_checkpoint as _load_checkpoint,
)
from agentos.distributed.postgres._commands import submit_command as _submit_command
from agentos.distributed.postgres._guards import lock_fenced_run
from agentos.distributed.postgres._outbox_records import STATUS_TOPIC, insert_outbox
from agentos.distributed.postgres._records import run_state_from_row
from agentos.distributed.postgres._state_records import (
    active_run,
    ensure_session,
    run_read_model,
    update_run,
)
from agentos.distributed.postgres._submissions import submit as _submit
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from agentos.runtime.checkpoint import RunCheckpoint, SessionCheckpoint
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand
from agentos.runtime.run_commit import RunTerminalStatus
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunAlreadyExistsError, RunState, RunStatus
from agentos.runtime.side_effect_types import WaitingToolCompletion


class PostgresStateStore:
    """管理租户范围内的 Run 提交和 PostgreSQL 聚合真值。"""

    def __init__(
        self,
        database: PostgresPool,
        *,
        scope: RequestScope | None = None,
        owns_database: bool = False,
    ) -> None:
        self._database = database
        self._scope = scope
        self._owns_database = owns_database

    @classmethod
    async def open(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        operation_timeout: timedelta = timedelta(seconds=5),
    ) -> PostgresStateStore:
        database = await PostgresPool.open(
            dsn,
            min_size=min_size,
            max_size=max_size,
            operation_timeout=operation_timeout,
        )
        store = cls(database, owns_database=True)
        try:
            await DistributedMigrationService(
                port=PostgresMigrationPort(database),
                plan=canonical_migration_plan(),
            ).check()
        except BaseException:
            await database.close()
            raise
        return store

    async def __aenter__(self) -> PostgresStateStore:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_database:
            await self._database.close()

    def bind(self, scope: RequestScope) -> PostgresStateStore:
        if type(scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        return PostgresStateStore(self._database, scope=scope)

    async def submit(
        self,
        *,
        scope: RequestScope,
        submission: RunSubmission,
    ) -> RunSubmissionReceipt:
        return await _submit(
            self._database,
            scope=scope,
            submission=submission,
        )

    async def get_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> RunReadModel | None:
        async with self._database.connection() as connection:
            row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_runs
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                """,
                (scope.tenant_id, session_id, run_id),
            )
        return None if row is None else run_read_model(row)

    async def get_active_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
    ) -> RunReadModel | None:
        async with self._database.connection() as connection:
            row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_runs
                WHERE tenant_id = %s AND session_id = %s
                  AND status IN ('created', 'queued', 'running', 'waiting')
                """,
                (scope.tenant_id, session_id),
            )
        return None if row is None else run_read_model(row)

    async def submit_command(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
    ) -> DurableCommandReceipt:
        return await _submit_command(
            self._database,
            scope=scope,
            session_id=session_id,
            command=command,
        )

    async def commit_running(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        return await _commit_running(
            self._database,
            self._require_bound_scope(),
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            guard=guard,
        )

    async def commit_waiting(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
        completion: WaitingToolCompletion | None = None,
    ) -> RunCheckpoint:
        return await _commit_waiting(
            self._database,
            self._require_bound_scope(),
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            reason=reason,
            guard=guard,
            completion=completion,
        )

    async def commit_terminal(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        status: RunTerminalStatus,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        return await _commit_terminal(
            self._database,
            self._require_bound_scope(),
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            status=status,
            guard=guard,
        )

    async def load_checkpoint(self, session_id: str) -> SessionCheckpoint | None:
        return await _load_checkpoint(
            self._database,
            self._require_bound_scope(),
            session_id,
        )

    async def latest_checkpoint(
        self,
        session_id: str,
        run_id: str,
    ) -> RunCheckpoint | None:
        return await _latest_checkpoint(
            self._database,
            self._require_bound_scope(),
            session_id,
            run_id,
        )

    async def create(self, state: RunState) -> RunState:
        scope = self._require_bound_scope()
        async with self._database.transaction() as connection:
            await ensure_session(connection, scope.tenant_id, state.session_id)
            if await active_run(connection, scope.tenant_id, state.session_id):
                raise RunAlreadyExistsError("session already has an active run")
            await connection.execute(
                """
                INSERT INTO agentos_distributed_runs
                    (tenant_id, session_id, run_id, status, aggregate_version)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    scope.tenant_id,
                    state.session_id,
                    state.run_id,
                    state.status.value,
                    state.aggregate_version,
                ),
            )
        return state

    async def get(self, *, session_id: str, run_id: str) -> RunState | None:
        scope = self._require_bound_scope()
        async with self._database.connection() as connection:
            row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_runs
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                """,
                (scope.tenant_id, session_id, run_id),
            )
        return None if row is None else run_state_from_row(row)

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
        del turn_id
        if status is not RunStatus.RUNNING or wait_reason is not None:
            raise CheckpointConflictError()
        scope = self._require_bound_scope()
        async with self._database.transaction() as connection:
            row = await lock_fenced_run(
                connection,
                tenant_id=scope.tenant_id,
                session_id=session_id,
                run_id=run_id,
                guard=guard,
            )
            current = run_state_from_row(row)
            updated = current.transition(RunStatus.RUNNING)
            claimed = await fetchone(
                connection,
                """
                SELECT turn_id FROM agentos_distributed_accepted_inputs
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                  AND status = 'claimed' AND claim_id = %s AND fencing_token = %s
                FOR UPDATE
                """,
                (
                    scope.tenant_id,
                    session_id,
                    run_id,
                    guard.claim_id,
                    guard.fencing_token,
                ),
            )
            if claimed is None:
                raise CheckpointConflictError()
            await update_run(connection, scope.tenant_id, updated)
            await insert_outbox(
                connection,
                scope=scope,
                session_id=session_id,
                run_id=run_id,
                source_kind="running",
                source_id=f"{run_id}:{updated.aggregate_version}",
                topic=STATUS_TOPIC,
                payload={
                    "status": "running",
                    "status_sequence": updated.aggregate_version,
                },
            )
        return updated

    def _require_bound_scope(self) -> RequestScope:
        if self._scope is None:
            raise ValueError("runtime store requires a bound RequestScope")
        return self._scope


__all__ = ["PostgresStateStore"]
