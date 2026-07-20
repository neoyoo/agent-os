from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from agentos._json_values import freeze_json_mapping
from agentos.context import WorkingStateField
from agentos.context.state import CompressedSegment
from agentos.distributed.errors import (
    CheckpointConflictError,
    StaleFenceError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._checkpoints import commit_terminal
from agentos.distributed.postgres._guards import lock_fenced_run
from agentos.runtime.checkpoint import (
    CheckpointStoredMessage,
    CheckpointToolCall,
    ContextCheckpoint,
    SessionCheckpoint,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from tests.planning._async import async_test


NOW = datetime(2026, 7, 20, tzinfo=UTC)
SCOPE = RequestScope("tenant_1", "principal_1")
INVOCATION_ID = "invocation_0123456789abcdef0123456789abcdef"


class Cursor:
    def __init__(
        self,
        *,
        row: dict[str, object] | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._row = row
        self._rows = [] if rows is None else rows

    async def fetchone(self) -> dict[str, object] | None:
        return self._row

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows


def _checkpoint(*messages: CheckpointStoredMessage) -> SessionCheckpoint:
    return SessionCheckpoint(
        session_id="session_1",
        session_status="running",
        next_turn_number=2,
        messages=messages,
        active_refs=tuple(message.id for message in messages),
        context=ContextCheckpoint(
            schema=(WorkingStateField("goal", "string", "task goal"),),
            working_state=freeze_json_mapping({}),
            compressed_history=(CompressedSegment("seg_1", "topic", "summary"),),
            inherited_state=(),
        ),
    )


def _running_row() -> dict[str, object]:
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
        "active_claim_expires_at": NOW + timedelta(minutes=1),
        "database_now": NOW,
    }


class TerminalConnection:
    def __init__(self) -> None:
        self.staged: list[str] = []

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> Cursor:
        del params
        normalized = " ".join(query.split())
        self.staged.append(normalized)
        if "SELECT r.*, s.fencing_token" in normalized:
            return Cursor(row=_running_row())
        if normalized.startswith("SELECT snapshot_json"):
            return Cursor()
        if normalized.startswith("INSERT INTO agentos_distributed_checkpoints"):
            return Cursor(row={"created_at": NOW})
        if (
            normalized.startswith("UPDATE agentos_distributed_accepted_inputs")
            and "RETURNING turn_id" in normalized
        ):
            return Cursor(row={"turn_id": "turn_1"})
        if normalized.startswith("INSERT INTO agentos_distributed_outbox"):
            raise RuntimeError("injected outbox failure")
        return Cursor()


class TransactionDatabase:
    def __init__(self) -> None:
        self.connection = TerminalConnection()
        self.committed: list[str] = []
        self.rolled_back = False

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        try:
            yield self.connection
        except BaseException:
            self.connection.staged.clear()
            self.rolled_back = True
            raise
        else:
            self.committed.extend(self.connection.staged)
            self.connection.staged.clear()


@async_test
async def test_terminal_outbox_failure_rolls_back_all_checkpoint_writes() -> None:
    database = TransactionDatabase()
    checkpoint = _checkpoint(
        CheckpointStoredMessage("message_1", "assistant", "final answer"),
    )

    with pytest.raises(RuntimeError, match="injected outbox failure"):
        await commit_terminal(  # type: ignore[arg-type]
            database,
            SCOPE,
            checkpoint=checkpoint,
            run_id="run_1",
            turn_id="turn_1",
            status="completed",
            guard=RunWriteGuard(1, "claim_1", 7),
        )

    assert database.rolled_back is True
    assert database.connection.staged == []
    assert database.committed == []


@async_test
async def test_invalid_terminal_history_never_opens_transaction() -> None:
    call = CheckpointToolCall(
        "provider_call_1",
        "lookup",
        "run_1",
        "turn_1",
        INVOCATION_ID,
        ProtectedPayloadRef("sealed", "digest"),
    )
    checkpoint = _checkpoint(
        CheckpointStoredMessage("message_1", "assistant", "", tool_calls=(call,)),
        CheckpointStoredMessage("message_2", "assistant", "final answer"),
    )

    class NeverDatabase:
        def transaction(self):  # type: ignore[no-untyped-def]
            raise AssertionError("invalid checkpoint reached database")

    with pytest.raises(CheckpointConflictError):
        await commit_terminal(  # type: ignore[arg-type]
            NeverDatabase(),
            SCOPE,
            checkpoint=checkpoint,
            run_id="run_1",
            turn_id="turn_1",
            status="completed",
            guard=RunWriteGuard(1, "claim_1", 7),
        )


@async_test
async def test_new_worker_fence_rejects_stale_worker_write() -> None:
    row = _running_row()
    row.update(
        session_fencing_token=8,
        active_claim_id="claim_new",
    )

    class GuardConnection:
        async def execute(
            self,
            query: str,
            params: tuple[object, ...] = (),
        ) -> Cursor:
            del query, params
            return Cursor(row=row)

    connection = GuardConnection()
    with pytest.raises(StaleFenceError):
        await lock_fenced_run(  # type: ignore[arg-type]
            connection,
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            guard=RunWriteGuard(1, "claim_old", 7),
        )

    current = await lock_fenced_run(  # type: ignore[arg-type]
        connection,
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        guard=RunWriteGuard(1, "claim_new", 8),
    )
    assert current is row
