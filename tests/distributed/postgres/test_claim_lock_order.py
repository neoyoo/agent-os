from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import os
from uuid import uuid4

import pytest

from agentos.distributed.models import ClaimedExecution, RequestScope, RunSubmission
from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    fetchone,
)
from agentos.distributed.postgres._submissions import submit
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from agentos.distributed.postgres.state import PostgresStateStore
from tests.planning._async import async_test


class _StopAfterLockOrder(Exception):
    pass


class _Cursor:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row


class _LockOrderConnection:
    def __init__(self) -> None:
        self.locks: list[str] = []

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> _Cursor:
        del params
        normalized = " ".join(query.split())
        if "FOR UPDATE OF o, r" in normalized:
            self.locks.append("run/outbox")
        elif (
            "FROM agentos_distributed_sessions" in normalized
            and "FOR UPDATE" in normalized
        ):
            self.locks.append("session")
        if len(self.locks) == 2:
            raise _StopAfterLockOrder
        if "FROM agentos_distributed_outbox AS o" in normalized:
            return _Cursor(
                {
                    "outbox_id": "outbox_1",
                    "tenant_id": "tenant_1",
                    "principal_id": "principal_1",
                    "session_id": "session_1",
                    "run_id": "run_1",
                    "status": "queued",
                    "wait_kind": None,
                    "wait_handle": None,
                    "wait_detail": None,
                    "wait_not_before": None,
                    "aggregate_version": 1,
                },
            )
        return _Cursor({"active_claim_id": None})


class _LockOrderDatabase:
    def __init__(self) -> None:
        self.connection = _LockOrderConnection()

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self.connection


class _SubmissionLockOrderConnection:
    def __init__(self) -> None:
        self.locks: list[str] = []

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> _Cursor:
        del params
        normalized = " ".join(query.split())
        if (
            normalized.startswith("SELECT session_id")
            and "FROM agentos_distributed_sessions" in normalized
            and "FOR UPDATE" in normalized
        ):
            self.locks.append("session")
        elif (
            "FROM agentos_distributed_runs" in normalized
            and "FOR UPDATE" in normalized
        ):
            self.locks.append("run")
        if len(self.locks) == 2:
            raise _StopAfterLockOrder
        return _Cursor()


class _SubmissionLockOrderDatabase:
    def __init__(self) -> None:
        self.connection = _SubmissionLockOrderConnection()

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self.connection


class _BorrowedTransactionDatabase:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        async with self._connection.transaction():
            yield self._connection


@async_test
async def test_claim_locks_session_before_run_and_outbox() -> None:
    database = _LockOrderDatabase()
    store = PostgresClaimStore(database)  # type: ignore[arg-type]

    with pytest.raises(_StopAfterLockOrder):
        await store.claim_pending_turn(
            scope=RequestScope("tenant_1", "principal_1"),
            outbox_id="outbox_1",
            owner_id="worker_1",
            ttl=timedelta(seconds=30),
        )

    assert database.connection.locks == ["session", "run/outbox"]


@async_test
async def test_submission_locks_session_before_active_run() -> None:
    database = _SubmissionLockOrderDatabase()

    with pytest.raises(_StopAfterLockOrder):
        await submit(  # type: ignore[arg-type]
            database,
            scope=RequestScope("tenant_1", "principal_1"),
            submission=RunSubmission("session_1", "submission_1", "question"),
        )

    assert database.connection.locks == ["session", "run"]


async def _assert_live_claim_succeeds(dsn: str) -> None:
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    database = await PostgresPool.open(dsn, min_size=0, max_size=3)
    try:
        state = PostgresStateStore(database)
        await DistributedMigrationService(
            port=PostgresMigrationPort(database),
            plan=canonical_migration_plan(),
        ).apply()
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(session_id, f"submission_{suffix}", "question"),
        )
        async with database.connection() as connection:
            row = await fetchone(
                connection,
                """
                SELECT outbox_id FROM agentos_distributed_outbox
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                """,
                (scope.tenant_id, session_id, receipt.run_id),
            )
        assert row is not None

        async with (
            database.connection() as blocker,
            database.connection() as claimant,
        ):
            claim_task: asyncio.Task[ClaimedExecution | None] | None = None
            claimed: ClaimedExecution | None = None
            try:
                async with blocker.transaction():
                    await blocker.execute(
                        """
                        SELECT session_id FROM agentos_distributed_sessions
                        WHERE tenant_id = %s AND session_id = %s FOR UPDATE
                        """,
                        (scope.tenant_id, session_id),
                    )
                    backend = await fetchone(
                        blocker,
                        "SELECT pg_backend_pid() AS pid",
                    )
                    assert backend is not None
                    claim_task = asyncio.create_task(
                        PostgresClaimStore(  # type: ignore[arg-type]
                            _BorrowedTransactionDatabase(claimant),
                        ).claim_pending_turn(
                            scope=scope,
                            outbox_id=str(row["outbox_id"]),
                            owner_id="worker_1",
                            ttl=timedelta(seconds=30),
                        ),
                    )
                    for _ in range(500):
                        waiting = await fetchone(
                            blocker,
                            """
                            SELECT EXISTS (
                                SELECT 1 FROM pg_stat_activity
                                WHERE %s = ANY(pg_blocking_pids(pid))
                            ) AS waiting
                            """,
                            (backend["pid"],),
                        )
                        if waiting is not None and waiting["waiting"] is True:
                            break
                    else:
                        raise AssertionError("claim did not wait on the locked session")
                    await blocker.execute(
                        """
                        SELECT run_id FROM agentos_distributed_runs
                        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                        FOR UPDATE NOWAIT
                        """,
                        (scope.tenant_id, session_id, receipt.run_id),
                    )
            finally:
                if claim_task is not None:
                    claimed = await claim_task

        assert claimed is not None
        assert claimed.claim.run_id == receipt.run_id
        assert claimed.target.session_id == session_id
    finally:
        await database.close()


@pytest.mark.integration
def test_live_claim_succeeds_with_session_first_lock_order() -> None:
    if not os.environ.get("AGENTOS_RUN_INTEGRATION"):
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 with a PostgreSQL test service")
    dsn = os.environ.get("AGENTOS_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set AGENTOS_TEST_POSTGRES_DSN")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_assert_live_claim_succeeds(dsn))
