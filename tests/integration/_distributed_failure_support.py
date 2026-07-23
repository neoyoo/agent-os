from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import os
from typing import Literal

import pytest

from agentos import AgentBuilder
from agentos._waiting import WaitReason
from agentos._builder_distributed import ClaimScopedAgentFactory
from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from agentos.distributed.models import ClaimedExecution, RequestScope
from agentos.distributed.postgres._database import PostgresPool, fetchall, fetchone
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres._side_effect_codec import (
    side_effect_record_from_json,
)
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from agentos.distributed.postgres.resume_validation import (
    PostgresSideEffectResumeValidator,
)
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.runtime.checkpoint import RunCheckpoint, SessionCheckpoint
from agentos.runtime.payloads import PayloadProtector
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunState, RunStatus
from agentos.runtime.side_effect_types import (
    SideEffectRecord,
    WaitingToolCompletion,
)


CrashPoint = Literal[
    "before_provider",
    "pending_tools",
    "after_tools",
    "run_start",
    "waiting",
]
CrashTiming = Literal["before", "after"]


class ProcessCrash(BaseException):
    pass


class NoopBlobStore:
    async def put_if_absent(self, *, key: str, data: bytes) -> bool:
        del key, data
        raise AssertionError("checkpoint recovery must not write artifact bytes")

    async def read(self, *, key: str) -> bytes:
        del key
        raise AssertionError("checkpoint recovery must not read artifact bytes")

    async def delete(self, *, key: str) -> None:
        del key

    async def close(self) -> None:
        pass


class CrashController:
    def __init__(self, point: CrashPoint, timing: CrashTiming) -> None:
        self.point = point
        self.timing = timing
        self.fired = False

    def crash(self, point: CrashPoint, timing: CrashTiming) -> None:
        if not self.fired and self.point == point and self.timing == timing:
            self.fired = True
            raise ProcessCrash


class CrashingStateStore(PostgresStateStore):
    def __init__(
        self,
        database: PostgresPool,
        controller: CrashController,
        *,
        scope: RequestScope | None = None,
    ) -> None:
        super().__init__(database, scope=scope)
        self._controller = controller

    def bind(self, scope: RequestScope) -> CrashingStateStore:
        return CrashingStateStore(
            self._database,
            self._controller,
            scope=scope,
        )

    async def commit_running(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        cursor = checkpoint.execution_cursor
        assert cursor is not None
        self._controller.crash(cursor.stage, "before")
        committed = await super().commit_running(
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            guard=guard,
        )
        self._controller.crash(cursor.stage, "after")
        return committed

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
        if status is RunStatus.RUNNING:
            self._controller.crash("run_start", "before")
        updated = await super().transition(
            session_id=session_id,
            run_id=run_id,
            status=status,
            wait_reason=wait_reason,
            guard=guard,
            turn_id=turn_id,
        )
        if status is RunStatus.RUNNING:
            self._controller.crash("run_start", "after")
        return updated

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
        self._controller.crash("waiting", "before")
        committed = await super().commit_waiting(
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            reason=reason,
            guard=guard,
            completion=completion,
        )
        self._controller.crash("waiting", "after")
        return committed


def claim_scoped_agent_factory(
    *,
    pool: PostgresPool,
    builder: AgentBuilder,
    state: PostgresStateStore,
    artifacts: PostgresArtifactStore,
    protector: PayloadProtector,
) -> ClaimScopedAgentFactory:
    return ClaimScopedAgentFactory(
        builder=builder,
        state_store=state,
        artifact_store=artifacts,
        side_effect_store=PostgresSideEffectStore(pool),
        side_effect_resume_validator=PostgresSideEffectResumeValidator(pool),
        payload_protector=protector,
    )


@dataclass(frozen=True, slots=True)
class LivePostgresSettings:
    dsn: str


def live_postgres_settings() -> LivePostgresSettings:
    if os.environ.get("AGENTOS_RUN_INTEGRATION") != "1":
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 to run live integration tests")
    dsn = os.environ.get("AGENTOS_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set AGENTOS_TEST_POSTGRES_DSN for live integration tests")
    return LivePostgresSettings(dsn)


async def open_migrated_pool(
    dsn: str,
    *,
    operation_timeout: timedelta = timedelta(seconds=5),
) -> PostgresPool:
    pool = await PostgresPool.open(
        dsn,
        min_size=0,
        max_size=4,
        operation_timeout=operation_timeout,
    )
    try:
        await DistributedMigrationService(
            port=PostgresMigrationPort(pool),
            plan=canonical_migration_plan(),
        ).apply()
    except BaseException:
        await pool.close()
        raise
    return pool


async def execution_outbox_id(
    pool: PostgresPool,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    latest: bool = False,
) -> str:
    direction = "DESC" if latest else "ASC"
    async with pool.connection() as connection:
        row = await fetchone(
            connection,
            f"""
            SELECT outbox_id FROM agentos_distributed_outbox
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
              AND topic = %s
            ORDER BY created_at {direction}, outbox_id {direction}
            LIMIT 1
            """,
            (scope.tenant_id, session_id, run_id, EXECUTION_TOPIC),
        )
    assert row is not None
    return str(row["outbox_id"])


async def side_effect_records(
    pool: PostgresPool,
    *,
    tenant_id: str,
    session_id: str,
) -> tuple[SideEffectRecord, ...]:
    async with pool.connection() as connection:
        rows = await fetchall(
            connection,
            """
            SELECT payload_json FROM agentos_distributed_side_effects
            WHERE tenant_id = %s AND session_id = %s
            ORDER BY operation_id, attempt
            """,
            (tenant_id, session_id),
        )
    records: list[SideEffectRecord] = []
    for row in rows:
        payload = row["payload_json"]
        assert type(payload) is str
        records.append(side_effect_record_from_json(payload))
    return tuple(records)


async def expire_and_reclaim(
    pool: PostgresPool,
    *,
    claims: PostgresClaimStore,
    scope: RequestScope,
    session_id: str,
    owner_id: str,
) -> ClaimedExecution:
    async with pool.transaction() as connection:
        await connection.execute(
            """
            UPDATE agentos_distributed_sessions
            SET active_claim_expires_at = clock_timestamp() - interval '1 second'
            WHERE tenant_id = %s AND session_id = %s
              AND active_claim_id IS NOT NULL
            """,
            (scope.tenant_id, session_id),
        )
    recovered = await claims.recover_expired(scope=scope, limit=1)
    assert len(recovered) == 1
    claimed = await claims.claim_pending_turn(
        scope=recovered[0].scope,
        outbox_id=recovered[0].outbox_id,
        owner_id=owner_id,
        ttl=timedelta(minutes=1),
    )
    assert claimed is not None
    return claimed


async def cleanup_tenant(pool: PostgresPool, tenant_id: str) -> None:
    async with pool.transaction() as connection:
        for table in (
            "agentos_distributed_side_effects",
            "agentos_distributed_execution_cursors",
            "agentos_distributed_reconciliation_sources",
            "agentos_distributed_checkpoints",
            "agentos_distributed_outbox",
            "agentos_distributed_commands",
            "agentos_distributed_accepted_inputs",
            "agentos_distributed_submissions",
            "agentos_distributed_runs",
            "agentos_distributed_artifact_deletions",
            "agentos_distributed_artifacts",
            "agentos_distributed_sessions",
        ):
            await connection.execute(
                f"DELETE FROM {table} WHERE tenant_id = %s",
                (tenant_id,),
            )


__all__ = [
    "CrashController",
    "CrashingStateStore",
    "LivePostgresSettings",
    "NoopBlobStore",
    "ProcessCrash",
    "claim_scoped_agent_factory",
    "cleanup_tenant",
    "execution_outbox_id",
    "expire_and_reclaim",
    "live_postgres_settings",
    "open_migrated_pool",
    "side_effect_records",
]
