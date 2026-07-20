from __future__ import annotations

from contextlib import asynccontextmanager

from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._claim_recovery import recover_expired
from tests.planning._async import async_test


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


class RecoveryConnection:
    def __init__(self) -> None:
        self.outbox_params: tuple[object, ...] | None = None

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> Cursor:
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT s.session_id"):
            return Cursor(rows=[{
                "session_id": "session_1",
                "run_id": "run_1",
                "fencing_token": 7,
                "principal_id": "principal_original",
                "status": "running",
                "wait_kind": None,
                "wait_handle": None,
                "wait_detail": None,
                "wait_not_before": None,
                "aggregate_version": 3,
            }])
        if normalized.startswith("UPDATE agentos_distributed_accepted_inputs"):
            return Cursor(row={"turn_id": "turn_1"})
        if normalized.startswith("INSERT INTO agentos_distributed_outbox"):
            self.outbox_params = params
        return Cursor()


class RecoveryDatabase:
    def __init__(self) -> None:
        self.connection = RecoveryConnection()

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self.connection


@async_test
async def test_recovery_preserves_the_accepted_input_principal() -> None:
    database = RecoveryDatabase()

    recovered = await recover_expired(  # type: ignore[arg-type]
        database,
        scope=RequestScope("tenant_1", "principal_reconciler"),
        limit=1,
    )

    assert recovered[0].scope == RequestScope("tenant_1", "principal_original")
    assert database.connection.outbox_params is not None
    assert database.connection.outbox_params[2] == "principal_original"
