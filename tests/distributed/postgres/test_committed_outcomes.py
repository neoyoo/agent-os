from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._committed_outcomes import input_matches_outbox
from agentos.distributed.postgres._identities import outbox_id
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.runtime.execution import ApplyAcceptedInput
from agentos.runtime.run_state import RunStatus


NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)
EXECUTION_OUTBOX_ID = outbox_id("tenant_1", "submission", "submission_1")


def _row(
    *,
    source_id: str = "submission_1",
    run_status: str = "completed",
    run_version: int = 4,
    checkpoint_version: int = 4,
    checkpoint_turn_id: str = "turn_1",
) -> dict[str, object]:
    return {
        "outbox_id": EXECUTION_OUTBOX_ID,
        "tenant_id": "tenant_1",
        "principal_id": "principal_1",
        "session_id": "session_1",
        "run_id": "run_1",
        "status": run_status,
        "wait_kind": None,
        "wait_handle": None,
        "wait_detail": None,
        "wait_not_before": None,
        "aggregate_version": run_version,
        "outbox_kind": "submission",
        "outbox_turn_id": None,
        "outbox_fencing_token": None,
        "outbox_recovery_id": None,
        "input_source_kind": "submission",
        "input_source_id": source_id,
        "input_status": "committed",
        "input_turn_id": "turn_1",
        "checkpoint_turn_id": checkpoint_turn_id,
        "checkpoint_fencing_token": 7,
        "checkpoint_aggregate_version": checkpoint_version,
        "checkpoint_created_at": NOW,
    }


class _Cursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> _Cursor:
        self.queries.append((" ".join(query.split()), params))
        return _Cursor(self.rows)


class _Database:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.connection_value = _Connection(rows)

    @asynccontextmanager
    async def connection(self):  # type: ignore[no-untyped-def]
        yield self.connection_value


class _ClaimCursor:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row


class _ClaimConnection:
    def __init__(
        self,
        *,
        outbox_turn_id: str = "turn_1",
        outbox_fencing_token: str = "8",
    ) -> None:
        recovery_id = "run_1:8"
        self.outbox_id = outbox_id("tenant_1", "recover", recovery_id)
        self.delivery_row = {
            "outbox_id": self.outbox_id,
            "tenant_id": "tenant_1",
            "principal_id": "principal_1",
            "session_id": "session_1",
            "run_id": "run_1",
            "status": "running",
            "wait_kind": None,
            "wait_handle": None,
            "wait_detail": None,
            "wait_not_before": None,
            "aggregate_version": 3,
            "outbox_kind": "recover",
            "outbox_turn_id": outbox_turn_id,
            "outbox_fencing_token": outbox_fencing_token,
            "outbox_recovery_id": recovery_id,
        }
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self.session_updated = False

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> _ClaimCursor:
        normalized = " ".join(query.split())
        self.queries.append((normalized, params))
        if "FROM agentos_distributed_outbox AS o" in normalized:
            return _ClaimCursor(self.delivery_row)
        if normalized.startswith("SELECT *, clock_timestamp() AS database_now"):
            return _ClaimCursor({"active_claim_id": None, "fencing_token": 8})
        if normalized.startswith("SELECT * FROM agentos_distributed_accepted_inputs"):
            return _ClaimCursor(
                {
                    "tenant_id": "tenant_1",
                    "principal_id": "principal_1",
                    "session_id": "session_1",
                    "run_id": "run_1",
                    "source_kind": "submission",
                    "source_id": "submission_1",
                    "content": "question",
                    "artifact_handles": [],
                    "turn_id": "turn_1",
                    "user_message_id": "message_1",
                },
            )
        if normalized.startswith("UPDATE agentos_distributed_sessions"):
            self.session_updated = True
            return _ClaimCursor(
                {
                    "fencing_token": 9,
                    "active_claim_expires_at": NOW + timedelta(seconds=30),
                },
            )
        if normalized.startswith("SELECT cursor.payload_json"):
            return _ClaimCursor()
        return _ClaimCursor()


class _ClaimDatabase:
    def __init__(self, connection: _ClaimConnection) -> None:
        self.connection_value = connection

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self.connection_value


def test_resolve_committed_outcome_binds_execution_outbox_to_checkpoint() -> None:
    async def scenario() -> None:
        database = _Database([_row(source_id="other"), _row()])
        store = PostgresClaimStore(database)  # type: ignore[arg-type]

        outcome = await store.resolve_committed_outcome(
            outbox_id=EXECUTION_OUTBOX_ID,
        )

        assert outcome is not None
        assert outcome.target.outbox_id == EXECUTION_OUTBOX_ID
        assert outcome.target.run.status is RunStatus.COMPLETED
        assert outcome.turn_id == "turn_1"
        assert outcome.execution_attempt == 7
        assert outcome.committed_version == 4
        assert outcome.committed_at == NOW
        assert outcome.is_current
        query, params = database.connection_value.queries[0]
        assert "o.topic = 'agentos.run.execution'" in query
        assert params == (EXECUTION_OUTBOX_ID,)

    asyncio.run(scenario())


def test_resolve_committed_outcome_marks_superseded_delivery() -> None:
    async def scenario() -> None:
        database = _Database(
            [_row(run_status="running", run_version=5, checkpoint_version=4)],
        )
        store = PostgresClaimStore(database)  # type: ignore[arg-type]

        outcome = await store.resolve_committed_outcome(
            outbox_id=EXECUTION_OUTBOX_ID,
        )

        assert outcome is not None
        assert not outcome.is_current
        assert outcome.target.run.status is RunStatus.RUNNING

    asyncio.run(scenario())


def test_resolve_committed_outcome_rejects_broken_checkpoint_association() -> None:
    async def scenario() -> None:
        database = _Database([_row(checkpoint_turn_id="turn_other")])
        store = PostgresClaimStore(database)  # type: ignore[arg-type]

        with pytest.raises(ClaimConflictError):
            await store.resolve_committed_outcome(
                outbox_id=EXECUTION_OUTBOX_ID,
            )

    asyncio.run(scenario())


def test_resolve_committed_outcome_returns_none_for_unrelated_input() -> None:
    async def scenario() -> None:
        database = _Database([_row(source_id="other")])
        store = PostgresClaimStore(database)  # type: ignore[arg-type]

        assert await store.resolve_committed_outcome(
            outbox_id=EXECUTION_OUTBOX_ID,
        ) is None

    asyncio.run(scenario())


def test_recover_outbox_binds_turn_and_recovery_fence() -> None:
    recovery_id = "run_1:8"
    recovery_outbox = outbox_id("tenant_1", "recover", recovery_id)
    input_row = {
        "source_kind": "submission",
        "source_id": "submission_1",
        "turn_id": "turn_1",
    }
    delivery_row = {
        "outbox_kind": "recover",
        "outbox_turn_id": "turn_1",
        "outbox_fencing_token": "8",
        "outbox_recovery_id": recovery_id,
    }

    assert input_matches_outbox(
        input_row,
        delivery_row,
        tenant_id="tenant_1",
        outbox_id=recovery_outbox,
        current_fencing_token=8,
    )
    assert not input_matches_outbox(
        input_row,
        delivery_row,
        tenant_id="tenant_1",
        outbox_id=recovery_outbox,
        current_fencing_token=9,
    )


def test_resolve_committed_outcome_supports_recover_outbox() -> None:
    async def scenario() -> None:
        recovery_id = "run_1:8"
        recovery_outbox = outbox_id("tenant_1", "recover", recovery_id)
        row = _row()
        row.update(
            outbox_id=recovery_outbox,
            outbox_kind="recover",
            outbox_turn_id="turn_1",
            outbox_fencing_token="8",
            outbox_recovery_id=recovery_id,
        )
        database = _Database([row])
        store = PostgresClaimStore(database)  # type: ignore[arg-type]

        outcome = await store.resolve_committed_outcome(outbox_id=recovery_outbox)

        assert outcome is not None
        assert outcome.target.outbox_id == recovery_outbox
        assert outcome.turn_id == "turn_1"

    asyncio.run(scenario())


def test_claim_pending_turn_wires_recover_outbox_to_current_input_and_fence() -> None:
    async def scenario() -> None:
        connection = _ClaimConnection()
        store = PostgresClaimStore(_ClaimDatabase(connection))  # type: ignore[arg-type]

        claimed = await store.claim_pending_turn(
            scope=RequestScope("tenant_1", "principal_1"),
            outbox_id=connection.outbox_id,
            owner_id="worker_1",
            ttl=timedelta(seconds=30),
        )

        assert claimed is not None
        assert claimed.target.outbox_id == connection.outbox_id
        assert claimed.execution.input.turn_id == "turn_1"
        assert type(claimed.execution.preparation) is ApplyAcceptedInput
        assert claimed.claim.fencing_token == 9
        assert connection.session_updated
        delivery_queries = [
            query
            for query, _ in connection.queries
            if "FROM agentos_distributed_outbox AS o" in query
        ]
        assert len(delivery_queries) == 2
        assert all("o.payload ->> 'turn_id' AS outbox_turn_id" in query for query in delivery_queries)
        assert all(
            "o.payload ->> 'fencing_token' AS outbox_fencing_token" in query
            for query in delivery_queries
        )
        assert all(
            "o.payload ->> 'recovery_id' AS outbox_recovery_id" in query
            for query in delivery_queries
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("outbox_turn_id", "outbox_fencing_token"),
    (("turn_other", "8"), ("turn_1", "7")),
)
def test_claim_pending_turn_rejects_stale_recover_binding(
    outbox_turn_id: str,
    outbox_fencing_token: str,
) -> None:
    async def scenario() -> None:
        connection = _ClaimConnection(
            outbox_turn_id=outbox_turn_id,
            outbox_fencing_token=outbox_fencing_token,
        )
        store = PostgresClaimStore(_ClaimDatabase(connection))  # type: ignore[arg-type]

        claimed = await store.claim_pending_turn(
            scope=RequestScope("tenant_1", "principal_1"),
            outbox_id=connection.outbox_id,
            owner_id="worker_1",
            ttl=timedelta(seconds=30),
        )

        assert claimed is None
        assert not connection.session_updated

    asyncio.run(scenario())
