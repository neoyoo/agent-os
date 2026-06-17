from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from agentos.multi.message_queue import QueueDelivery
from agentos.multi.postgres_team import PostgresTeamWorkerRetryStore
from agentos.multi.team import TeamMessage, TeamWorkerRetryRecord
from agentos.multi.types import AgentEnvelope


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], dict[str, object]] = {}
        self.sql: list[str] = []
        self.commits = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if "INSERT INTO agentos_team_worker_retries" in sql:
            payload = json.loads(str(params[8]))
            self.records[(str(params[1]), str(params[3]))] = {
                "team_id": params[0],
                "agent_id": params[1],
                "session_id": params[2],
                "delivery_id": params[3],
                "status": params[4],
                "next_run_at": params[5],
                "attempts": params[6],
                "exhausted_at": params[7],
                "payload": payload,
            }
            return FakeCursor()
        if "SELECT payload FROM agentos_team_worker_retries" in sql and (
            "agent_id = %s AND delivery_id = %s" in sql
        ):
            row = self.records.get((str(params[0]), str(params[1])))
            return FakeCursor([(row["payload"],)] if row else [])
        if "SELECT payload FROM agentos_team_worker_retries" in sql:
            rows = []
            for row in sorted(
                self.records.values(),
                key=lambda item: (
                    float(item["next_run_at"]),
                    str(item["team_id"]),
                    str(item["agent_id"]),
                    str(item["delivery_id"]),
                ),
            ):
                if params and str(row["team_id"]) != str(params[0]):
                    continue
                rows.append((row["payload"],))
            return FakeCursor(rows)
        if "DELETE FROM agentos_team_worker_retries" in sql:
            self.records.pop((str(params[0]), str(params[1])), None)
            return FakeCursor()
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1


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


def retry_record(
    delivery_id: str = "delivery_1",
    *,
    next_run_at: float = 20.0,
    attempts: int = 1,
    status: str = "scheduled",
) -> TeamWorkerRetryRecord:
    return TeamWorkerRetryRecord(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id=delivery_id,
        message_id="message_1",
        attempts=attempts,
        status=status,  # type: ignore[arg-type]
        next_run_at=next_run_at,
        last_error="worker failed",
        delivery=QueueDelivery(
            delivery_id=delivery_id,
            envelope=AgentEnvelope(
                envelope_id="env_1",
                from_agent_id="leader",
                to_agent_id="worker",
                type="team_message",
                payload=TeamMessage(
                    message_id="message_1",
                    team_id="team_1",
                    from_agent_id="leader",
                    to_agent_id="worker",
                    content="Find source A.",
                    kind="instruction",
                    created_at=10.0,
                ),
                created_at=10.0,
                correlation_id="message_1",
            ),
        ),
        exhausted_at=next_run_at if status == "exhausted" else None,
    )


def test_postgres_team_worker_retry_store_round_trips_record_with_delivery() -> None:
    connection = FakeConnection()
    store = PostgresTeamWorkerRetryStore(
        dsn="postgresql://unused",
        connection=connection,
    )

    record = retry_record()
    store.record_failure(record)

    assert store.get("worker", "delivery_1") == record
    assert connection.commits == 1
    joined_sql = "\n".join(connection.sql)
    assert "ON CONFLICT (agent_id, delivery_id) DO UPDATE" in joined_sql
    assert "payload = EXCLUDED.payload" in joined_sql


def test_postgres_team_worker_retry_store_from_pool_borrows_per_operation() -> None:
    connection = FakeConnection()
    pool = FakePool(connection)
    store = PostgresTeamWorkerRetryStore.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    assert pool.gets == 0

    store.record_failure(retry_record())
    assert pool.gets == 1
    assert pool.puts == [connection]

    assert store.get("worker", "delivery_1") == retry_record()
    assert pool.gets == 2
    assert pool.puts == [connection, connection]

    store.close()
    assert pool.puts == [connection, connection]


def test_postgres_team_worker_retry_store_lists_records_ordered_and_filters_team() -> None:
    connection = FakeConnection()
    store = PostgresTeamWorkerRetryStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    store.record_failure(retry_record("delivery_2", next_run_at=30.0))
    store.record_failure(retry_record("delivery_1", next_run_at=20.0))
    other_team = replace(
        retry_record("delivery_3", next_run_at=10.0),
        team_id="team_2",
    )
    store.record_failure(other_team)

    assert store.list_records("team_1") == (
        retry_record("delivery_1", next_run_at=20.0),
        retry_record("delivery_2", next_run_at=30.0),
    )
    assert store.list_records() == (
        other_team,
        retry_record("delivery_1", next_run_at=20.0),
        retry_record("delivery_2", next_run_at=30.0),
    )


def test_postgres_team_worker_retry_store_clears_record() -> None:
    connection = FakeConnection()
    store = PostgresTeamWorkerRetryStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    store.record_failure(retry_record())

    store.clear("worker", "delivery_1")

    assert store.get("worker", "delivery_1") is None
    assert connection.commits == 2


def test_postgres_team_worker_retry_migration_defines_table_and_indexes() -> None:
    migration = Path(
        "docs/migrations/2026-06-12-postgres-team-worker-retries.sql",
    ).read_text(encoding="utf-8")

    assert "-- migrate:up" in migration
    assert "-- migrate:down" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_team_worker_retries" in migration
    assert "PRIMARY KEY (agent_id, delivery_id)" in migration
    assert "payload JSONB NOT NULL" in migration
    assert "agentos_team_worker_retries_due_idx" in migration
