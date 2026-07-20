from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest

from agentos.capabilities.tools import SideEffectPolicy
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres._guards import lock_fenced_run
from agentos.distributed.postgres._side_effect_codec import side_effect_record_to_json
from agentos.distributed.postgres._side_effect_composite import apply_cancel_safe_stop
from agentos.distributed.postgres._side_effect_records import require_current
from agentos.distributed.postgres.schema import initialize_postgres_schema
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectStatus,
    SideEffectTransitionError,
)
from tests.planning._async import async_test


OPERATION_ID = "operation_0123456789abcdef0123456789abcdef"
INVOCATION_ID = "invocation_0123456789abcdef0123456789abcdef"


class _Cursor:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row

    async def fetchall(self) -> list[dict[str, object]]:
        return [] if self._row is None else [self._row]


def _record(
    *,
    tenant_id: str = "tenant_1",
    session_id: str = "session_1",
) -> SideEffectRecord:
    return SideEffectRecord(
        attempt_id=SideEffectAttemptId(tenant_id, session_id, OPERATION_ID, 1),
        run_id="run_1",
        turn_id="turn_1",
        invocation_id=INVOCATION_ID,
        tool_name="charge",
        policy=SideEffectPolicy.DEDUPLICATED,
        status=SideEffectStatus.RESERVED,
        invocation_digest="sha256:" + "a" * 64,
        invocation_ref=ProtectedPayloadRef("sealed", "digest"),
        claim_id="claim_1",
        fencing_token=7,
    )


def _record_row(record: SideEffectRecord) -> dict[str, object]:
    return {
        "tenant_id": record.attempt_id.tenant_id,
        "session_id": record.attempt_id.session_id,
        "operation_id": record.attempt_id.operation_id,
        "attempt": record.attempt_id.attempt,
        "run_id": record.run_id,
        "status": record.status.value,
        "payload_json": side_effect_record_to_json(record),
    }


def _run_row() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "run_id": "run_1",
        "session_id": "session_1",
        "status": "running",
        "wait_kind": None,
        "wait_handle": None,
        "wait_detail": None,
        "wait_not_before": None,
        "aggregate_version": 1,
        "session_fencing_token": 7,
        "active_claim_id": "claim_1",
        "active_claim_run_id": "run_1",
        "active_claim_expires_at": now + timedelta(minutes=5),
        "database_now": now,
    }


class _LockOrderConnection:
    def __init__(
        self,
        record: SideEffectRecord,
        *,
        locked_record: SideEffectRecord | None = None,
    ) -> None:
        self._record = record
        self._locked_record = record if locked_record is None else locked_record
        self.lock_order: list[str] = []

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> _Cursor:
        del params
        normalized = " ".join(query.split())
        if "FOR UPDATE" in normalized:
            if "FROM agentos_distributed_sessions" in normalized:
                self.lock_order.append("session")
            elif "FROM agentos_distributed_runs" in normalized:
                self.lock_order.append("run")
            elif "FROM agentos_distributed_side_effects" in normalized:
                self.lock_order.append("side_effect")
        if (
            "FROM agentos_distributed_sessions" in normalized
            or "FROM agentos_distributed_runs" in normalized
        ):
            return _Cursor(_run_row())
        selected = self._locked_record if "FOR UPDATE" in normalized else self._record
        return _Cursor(_record_row(selected))


class _TransactionDatabase:
    def __init__(self, connection: _LockOrderConnection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self._connection


@async_test
async def test_guarded_side_effect_locks_run_before_ledger() -> None:
    record = _record()
    connection = _LockOrderConnection(record)

    current = await require_current(  # type: ignore[arg-type]
        connection,
        record.attempt_id,
        RunWriteGuard(1, "claim_1", 7),
    )

    assert current == record
    assert connection.lock_order == ["session", "run", "side_effect"]


@pytest.mark.parametrize("attempt", [None, 1])
def test_side_effect_get_locks_run_before_ledger(attempt: int | None) -> None:
    async def verify() -> None:
        record = _record()
        connection = _LockOrderConnection(record)
        store = PostgresSideEffectStore(  # type: ignore[arg-type]
            _TransactionDatabase(connection),
        )

        current = await store.get(
            tenant_id="tenant_1",
            session_id="session_1",
            operation_id=OPERATION_ID,
            attempt=attempt,
            guard=RunWriteGuard(1, "claim_1", 7),
        )

        assert current == record
        assert connection.lock_order == ["session", "run", "side_effect"]

    asyncio.run(verify())


@pytest.mark.parametrize("access", ["transition", "get"])
def test_side_effect_access_rejects_owner_change_after_fence(access: str) -> None:
    async def verify() -> None:
        candidate = _record()
        connection = _LockOrderConnection(
            candidate,
            locked_record=replace(candidate, run_id="run_2"),
        )
        guard = RunWriteGuard(1, "claim_1", 7)

        with pytest.raises(SideEffectTransitionError):
            if access == "transition":
                await require_current(connection, candidate.attempt_id, guard)  # type: ignore[arg-type]
            else:
                store = PostgresSideEffectStore(  # type: ignore[arg-type]
                    _TransactionDatabase(connection),
                )
                await store.get(
                    tenant_id="tenant_1",
                    session_id="session_1",
                    operation_id=OPERATION_ID,
                    attempt=None,
                    guard=guard,
                )

    asyncio.run(verify())


class _ObservedConnection:
    def __init__(self, connection: object, session_lock_attempted: asyncio.Event) -> None:
        self._connection = connection
        self._session_lock_attempted = session_lock_attempted

    async def execute(self, query: str, params: tuple[object, ...] = ()):
        normalized = " ".join(query.split())
        if (
            "FROM agentos_distributed_sessions" in normalized
            and "FOR UPDATE" in normalized
        ):
            self._session_lock_attempted.set()
        return await self._connection.execute(query, params)  # type: ignore[union-attr]


class _SingleConnectionDatabase:
    def __init__(self, connection: object, session_lock_attempted: asyncio.Event) -> None:
        self._connection = connection
        self._session_lock_attempted = session_lock_attempted

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        async with self._connection.transaction():  # type: ignore[union-attr]
            yield _ObservedConnection(self._connection, self._session_lock_attempted)


def _require_live_postgres() -> str:
    if not os.environ.get("AGENTOS_RUN_INTEGRATION"):
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 with a test PostgreSQL service")
    dsn = os.environ.get("AGENTOS_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set AGENTOS_TEST_POSTGRES_DSN")
    return dsn


@pytest.mark.integration
@pytest.mark.parametrize("action", ["transition", "get"])
def test_live_cancel_does_not_deadlock_with_side_effect_access(action: str) -> None:
    loop = asyncio.SelectorEventLoop()
    try:
        loop.run_until_complete(_verify_live_cancel_lock_order(action))
    finally:
        loop.close()


async def _verify_live_cancel_lock_order(action: str) -> None:
    dsn = _require_live_postgres()
    unique = uuid4().hex
    tenant_id = f"tenant_lock_{unique}"
    session_id = f"session_lock_{unique}"
    record = _record(tenant_id=tenant_id, session_id=session_id)
    attempt_id = record.attempt_id
    guard = RunWriteGuard(1, "claim_1", 7)
    database = await PostgresPool.open(dsn, min_size=1, max_size=3)
    access: asyncio.Task[SideEffectRecord | None] | None = None
    try:
        async with database.transaction() as setup:
            await initialize_postgres_schema(setup)
            await setup.execute(
                """
                INSERT INTO agentos_distributed_sessions
                    (tenant_id, session_id, status, next_turn_number, fencing_token,
                     active_claim_id, active_claim_owner_id, active_claim_run_id,
                     active_claim_expires_at)
                VALUES (%s, %s, 'running', 2, 7, 'claim_1', 'worker_1', 'run_1',
                        clock_timestamp() + interval '5 minutes')
                """,
                (tenant_id, session_id),
            )
            await setup.execute(
                """
                INSERT INTO agentos_distributed_runs
                    (tenant_id, session_id, run_id, status, aggregate_version)
                VALUES (%s, %s, 'run_1', 'running', 1)
                """,
                (tenant_id, session_id),
            )
            await setup.execute(
                """
                INSERT INTO agentos_distributed_side_effects
                    (tenant_id, session_id, operation_id, attempt, run_id,
                     status, payload_json)
                VALUES (%s, %s, %s, 1, 'run_1', 'reserved', %s)
                """,
                (
                    tenant_id,
                    session_id,
                    OPERATION_ID,
                    side_effect_record_to_json(record),
                ),
            )

        session_lock_attempted = asyncio.Event()
        async with database.connection() as holder, database.connection() as effect:
            async with holder.transaction():
                await lock_fenced_run(
                    holder,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id="run_1",
                    guard=guard,
                )
                store = PostgresSideEffectStore(
                    _SingleConnectionDatabase(effect, session_lock_attempted),  # type: ignore[arg-type]
                )
                access = asyncio.create_task(
                    store.mark_started(attempt_id=attempt_id, guard=guard)
                    if action == "transition"
                    else store.get(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        operation_id=OPERATION_ID,
                        attempt=None,
                        guard=guard,
                    ),
                )
                await asyncio.wait_for(session_lock_attempted.wait(), timeout=2)
                await holder.execute("SET LOCAL lock_timeout = '500ms'")
                await apply_cancel_safe_stop(
                    holder,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id="run_1",
                )

            if action == "transition":
                with pytest.raises(SideEffectTransitionError):
                    await asyncio.wait_for(access, timeout=2)
            else:
                loaded = await asyncio.wait_for(access, timeout=2)
                assert loaded is not None
                assert loaded.status is SideEffectStatus.RESOLVED
    finally:
        if access is not None and not access.done():
            access.cancel()
            await asyncio.gather(access, return_exceptions=True)
        async with database.transaction() as cleanup:
            await cleanup.execute(
                "DELETE FROM agentos_distributed_side_effects WHERE tenant_id = %s",
                (tenant_id,),
            )
            await cleanup.execute(
                "DELETE FROM agentos_distributed_runs WHERE tenant_id = %s",
                (tenant_id,),
            )
            await cleanup.execute(
                "DELETE FROM agentos_distributed_sessions WHERE tenant_id = %s",
                (tenant_id,),
            )
        await database.close()
