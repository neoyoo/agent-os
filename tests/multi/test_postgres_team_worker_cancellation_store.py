from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from agentos.multi.postgres_team import PostgresTeamWorkerCancellationStore
from agentos.multi.team import TeamWorkerCancellationRecord


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.records: dict[
            tuple[str, str, str, str],
            dict[str, object],
        ] = {}
        self.sql: list[str] = []
        self.commits = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if "INSERT INTO agentos_team_worker_cancellations" in sql:
            payload = json.loads(str(params[10]))
            key = self._key(params[1], params[2], params[3], params[4])
            self.records[key] = {
                "team_id": params[0],
                "agent_id": params[1],
                "session_id": params[2],
                "delivery_key": self._nullable_key(params[3]),
                "message_key": self._nullable_key(params[4]),
                "delivery_id": params[3],
                "message_id": params[4],
                "status": params[5],
                "requested_at": params[6],
                "acknowledged_at": params[7],
                "cleared_at": params[8],
                "reason": params[9],
                "payload": payload,
            }
            return FakeCursor()
        if "SELECT payload FROM agentos_team_worker_cancellations" in sql and (
            "status = 'requested'" in sql
        ):
            rows = [
                row
                for row in self.records.values()
                if self._matches_active(row, params)
            ]
            rows.sort(
                key=lambda row: (
                    0
                    if row["delivery_id"] is not None
                    or row["message_id"] is not None
                    else 1,
                    float(row["requested_at"]),
                    str(row["delivery_key"]),
                    str(row["message_key"]),
                ),
            )
            return FakeCursor([(row["payload"],) for row in rows[:1]])
        if "UPDATE agentos_team_worker_cancellations" in sql:
            status = str(params[0])
            acknowledged_at = params[1]
            cleared_at = params[2]
            payload = json.loads(str(params[3]))
            key = (
                str(params[4]),
                str(params[5]),
                str(params[6]),
                str(params[7]),
            )
            if key in self.records:
                self.records[key]["status"] = status
                self.records[key]["acknowledged_at"] = acknowledged_at
                self.records[key]["cleared_at"] = cleared_at
                self.records[key]["payload"] = payload
            return FakeCursor()
        if "SELECT payload FROM agentos_team_worker_cancellations" in sql:
            rows = []
            for row in sorted(
                self.records.values(),
                key=lambda item: (
                    str(item["team_id"]),
                    str(item["agent_id"]),
                    str(item["session_id"]),
                    float(item["requested_at"]),
                    str(item["delivery_key"]),
                    str(item["message_key"]),
                ),
            ):
                if params and str(row["team_id"]) != str(params[0]):
                    continue
                rows.append((row["payload"],))
            return FakeCursor(rows)
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1

    def _matches_active(
        self,
        row: dict[str, object],
        params: tuple[object, ...],
    ) -> bool:
        team_id, agent_id, session_id, delivery_id, message_id = params
        if row["status"] != "requested":
            return False
        if str(row["team_id"]) != str(team_id):
            return False
        if str(row["agent_id"]) != str(agent_id):
            return False
        if str(row["session_id"]) != str(session_id):
            return False
        if row["delivery_id"] is not None and row["delivery_id"] == delivery_id:
            return True
        if row["message_id"] is not None and row["message_id"] == message_id:
            return True
        return row["delivery_id"] is None and row["message_id"] is None

    def _key(
        self,
        agent_id: object,
        session_id: object,
        delivery_id: object,
        message_id: object,
    ) -> tuple[str, str, str, str]:
        return (
            str(agent_id),
            str(session_id),
            self._nullable_key(delivery_id),
            self._nullable_key(message_id),
        )

    def _nullable_key(self, value: object) -> str:
        return "null:" if value is None else f"value:{value}"


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.gets = 0
        self.puts: list[FakeConnection] = []

    def getconn(self) -> FakeConnection:
        self.gets += 1
        return self.connection

    def putconn(self, connection: FakeConnection) -> None:
        self.puts.append(connection)


def cancellation_record(
    *,
    team_id: str = "team_1",
    agent_id: str = "worker",
    session_id: str = "session_worker",
    delivery_id: str | None = "delivery_1",
    message_id: str | None = None,
    requested_at: float = 10.0,
    status: str = "requested",
) -> TeamWorkerCancellationRecord:
    return TeamWorkerCancellationRecord(
        team_id=team_id,
        agent_id=agent_id,
        session_id=session_id,
        reason="leader cancelled",
        requested_at=requested_at,
        status=status,  # type: ignore[arg-type]
        delivery_id=delivery_id,
        message_id=message_id,
        acknowledged_at=11.0 if status == "acknowledged" else None,
        cleared_at=12.0 if status == "cleared" else None,
    )


def test_postgres_team_worker_cancellation_store_matches_delivery_record() -> None:
    connection = FakeConnection()
    store = PostgresTeamWorkerCancellationStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    record = cancellation_record()

    store.request_cancel(record)

    assert store.match(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id="delivery_1",
    ) == record
    joined_sql = "\n".join(connection.sql)
    assert "ON CONFLICT" in joined_sql
    assert "payload = EXCLUDED.payload" in joined_sql
    assert connection.commits == 1


def test_postgres_team_worker_cancellation_store_from_pool_borrows_per_operation() -> None:
    connection = FakeConnection()
    pool = FakePool(connection)
    store = PostgresTeamWorkerCancellationStore.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    assert pool.gets == 0

    store.request_cancel(cancellation_record())
    assert pool.gets == 1
    assert pool.puts == [connection]

    assert store.match(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id="delivery_1",
    ) == cancellation_record()
    assert pool.gets == 2
    assert pool.puts == [connection, connection]

    store.close()
    assert pool.puts == [connection, connection]


def test_postgres_team_worker_cancellation_store_prefers_exact_over_worker_scope() -> None:
    connection = FakeConnection()
    store = PostgresTeamWorkerCancellationStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    worker_scope = cancellation_record(delivery_id=None, requested_at=1.0)
    exact = cancellation_record(delivery_id="delivery_1", requested_at=2.0)
    message_exact = cancellation_record(
        delivery_id=None,
        message_id="message_1",
        requested_at=3.0,
    )

    store.request_cancel(worker_scope)
    store.request_cancel(exact)
    store.request_cancel(message_exact)

    assert store.match(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id="delivery_1",
        message_id="message_1",
    ) == exact
    assert store.match(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id="delivery_2",
        message_id="message_1",
    ) == message_exact
    assert store.match(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id="delivery_2",
        message_id="message_2",
    ) == worker_scope


def test_postgres_team_worker_cancellation_store_acknowledges_and_clears() -> None:
    connection = FakeConnection()
    store = PostgresTeamWorkerCancellationStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    record = cancellation_record()
    store.request_cancel(record)

    acknowledged = store.acknowledge(record, now=20.0)
    cleared = store.clear(acknowledged, now=30.0)

    assert acknowledged == replace(
        record,
        status="acknowledged",
        acknowledged_at=20.0,
    )
    assert cleared == replace(
        acknowledged,
        status="cleared",
        cleared_at=30.0,
    )
    assert store.match(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id="delivery_1",
    ) is None
    assert connection.commits == 3


def test_postgres_team_worker_cancellation_store_lists_records_by_team() -> None:
    connection = FakeConnection()
    store = PostgresTeamWorkerCancellationStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    first = cancellation_record(delivery_id=None, requested_at=1.0)
    second = cancellation_record(delivery_id="delivery_2", requested_at=2.0)
    other_team = cancellation_record(
        team_id="team_2",
        delivery_id="delivery_3",
        requested_at=0.5,
    )

    store.request_cancel(second)
    store.request_cancel(other_team)
    store.request_cancel(first)

    assert store.list_records("team_1") == (first, second)
    assert store.list_records() == (first, second, other_team)


def test_postgres_team_worker_cancellation_migration_defines_table_and_indexes() -> None:
    migration = Path(
        "docs/migrations/2026-06-15-postgres-team-worker-cancellations.sql",
    ).read_text(encoding="utf-8")

    assert "-- migrate:up" in migration
    assert "-- migrate:down" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_team_worker_cancellations" in migration
    assert "payload JSONB NOT NULL" in migration
    assert "agentos_team_worker_cancellations_identity_idx" in migration
    assert "agentos_team_worker_cancellations_active_idx" in migration
    assert "DROP TABLE IF EXISTS agentos_team_worker_cancellations" in migration
